"""
Lagrangian Multiphase Plume
===========================

This module contains the numerical solution for the `bent_plume_model` module.
Some of the general tools for handling the multiphase components are
contained in `dispersed_phases`.

"""
# S. Socolofsky, November 2014, Texas A&M University <socolofs@tamu.edu>.
from tamoc import seawater
from tamoc import dispersed_phases

import numpy as np
from scipy import integrate
from copy import deepcopy

def derivs(t, q, q0_local, q1_local, profile, p, particles):
    """
    Calculate the derivatives for the system of ODEs for a Lagrangian plume
    
    Calculates the right-hand-side of the system of ODEs for a Lagrangian 
    plume integral model.  The continuous phase model matches very closely 
    the model of Lee and Cheung (1990), with adaptations for the shear 
    entrainment following Jirka (2004).  Multiphase extensions following the
    strategy in Socolofsky et al. (2008) with adaptation to Lagrangian plume
    models by Johansen (2000, 2003) and Yapa and Zheng (1997).  
    
    Parameters
    ----------
    t : float
        Current value for the independent variable (time in s).
    q : ndarray
        Current value for the plume state space vector.
    q0_local : `bent_plume_model.LagElement`
        Object containing the numerical solution at the previous time step
    q1_local : `bent_plume_model.LagElement`
        Object containing the numerical solution at the current time step
    profile : `ambient.Profile` object
        The ambient CTD object used by the simulation.
    p : `ModelParams` object
        Object containing the fixed model parameters for the bent
        plume model.
    particles : list of `Particle` objects
        List of `bent_plume_model.Particle` objects containing the dispersed 
        phase local conditions and behavior.
    
    Returns
    -------
    yp : ndarray
        A vector of the derivatives of the plume state space.
    
    See Also
    --------
    calculate
    
    """
    # Set up the output from the function to have the right size and shape
    qp = np.zeros(q.shape)
    
    # Update the local Lagrangian element properties
    q1_local.update(t, q, profile, p, particles)
    
    # Get the entrainment flux
    md = entrainment(q0_local, q1_local, p)
    
    # Get the dispersed phase tracking variables
    (fe, up, dtp_dt) = track_particles(q0_local, q1_local, md, particles)
    
    # Conservation of Mass
    qp[0] = md
    
    # Conservation of salt and heat
    qp[1] = md * q1_local.Sa
    qp[2] = md * seawater.cp() * q1_local.Ta
    
    # Conservation of continuous phase momentum.  Note that z is positive
    # down (depth).
    qp[3] = md * q1_local.ua
    qp[4] = 0.
    qp[5] = - p.g / (p.gamma * p.rho_r) * (q1_local.Fb + q1_local.M * 
            (q1_local.rho_a - q1_local.rho))
    
    # Constant h/V thickeness to velocity ratio
    qp[6] = 0.
    
    # Lagrangian plume element advection (x, y, z) and s along the centerline
    # trajectory
    qp[7] = q1_local.u
    qp[8] = q1_local.v
    qp[9] = q1_local.w
    qp[10] = q1_local.V
    
    # Conservation equations for each dispersed phase
    idx = 11
    
    # Track the mass dissolving into the continuous phase per unit time
    dm = np.zeros(q1_local.nchems)
    
    # Compute mass and heat transfer for each particle
    for i in range(len(particles)):
        
        # Track each particle's mass transfer separately
        dm_p = np.zeros(q1_local.nchems)
        
        # Dissolution
        if particles[i].particle.issoluble:
            for j in range(q1_local.nchems):
                
                # Conservation of particle mass for a single chemical j
                qp[idx] = - particles[i].A * particles[i].nbe * \
                          particles[i].beta[j] * (particles[i].Cs[j] - 
                          q1_local.c_chems[j]) * dtp_dt[i]
                dm_p[j] = qp[idx]
                
                # Update continuous phase temperature with heat of solution
                qp[2] += qp[idx] * particles[i].particle.neg_dH_solR[j] * p.Ru / \
                         particles[i].particle.M[j]
                idx += 1
            
        else:
            # Non-dissolving particles have one component
            qp[idx] = 0.
            idx += 1
        
        # Update the total mass dissolved
        dm += dm_p
        
        # Heat transfer between the particle and the ambient
        qp[idx] = - particles[i].A * particles[i].nbe * particles[i].rho_p * \
                  particles[i].cp * particles[i].beta_T * (particles[i].T - 
                  q1_local.T) * dtp_dt[i]
        
        # Heat loss due to mass loss by dissolution
        qp[idx] += np.sum(dm_p) * particles[i].cp * particles[i].T
        
        # Take the heat leaving the particle and put it in the continuous 
        # phase fluid
        qp[2] -= qp[idx]
        idx += 1
        
        # Track the particles in the plume
        if particles[i].integrate:
            # Particle age
            qp[idx] = dtp_dt[i]
            idx += 1
            
            # Follow the particles in the local coordinate system (l,n,m) 
            # relative to the plume centerline
            qp[idx] = 0.
            idx += 1
            qp[idx] = (up[i,1] - fe * q[idx]) * dtp_dt[i]
            idx += 1
            qp[idx] = (up[i,2] - fe * q[idx]) * dtp_dt[i]
            idx += 1
            
        else:
            idx += 4
    
    # Conservation equations for the dissolved constituents in the plume
    for i in range(q1_local.nchems):
        qp[idx] = md / q1_local.rho_a * q1_local.ca_chems[i] - dm[i]
        idx += 1
    
    # Conservation equation for the passive tracers in the plume
    qp[idx:] = md * q1_local.ca_tracers
    
    # Return the slopes
    return qp


def calculate(t0, q0, q0_local, profile, p, particles, derivs, dt_max, 
              sd_max):
    """
    Integrate an the Lagrangian plume solution
    
    Compute the solution tracking along the centerline of the plume until 
    the plume reaches the water surface, reaches a neutral buoyancy level 
    within the intrusion layer, or propagates a given maximum number of
    nozzle diameters downstream.
    
    Parameters
    ----------
    t0 : float
        Initial time (s)
    q0 : ndarray
        Initial values of the state space vector
    q0_local : `bent_plume_model.LagElement`
        Object containing the numerical solution at the initial condition
    profile : `ambient.Profile` object
        The ambient CTD object used by the simulation.
    p : `ModelParams` object
        Object containing the fixed model parameters for the bent
        plume model.
    particles : list of `Particle` objects
        List of `bent_plume_model.Particle` objects containing the dispersed 
        phase local conditions and behavior.
    derivs : function handle
        Pointer to the function where the derivatives of the ODE system are
        stored.  Should be `lmp.derivs`.
    dt_max : float
        Maximum step size to use in the simulation (s).  The ODE solver 
        in `calculate` is set up with adaptive step size integration, so 
        this value determines the largest step size in the output data, but 
        not the numerical stability of the calculation.
    sd_max : float
        Maximum number of nozzle diameters to compute along the plume 
        centerline (s/D)_max.  This is the only stop criteria that is user-
        selectable.
    
    Returns
    -------
    t : ndarray
        Vector of times when the plume solution is obtained (s).
    y : ndarray
        Matrix of the plume state space solutions.  Each row corresponds to
        a time in `t`.
    
    See Also
    --------
    derivs, bent_plume_mode.Model
        
    """
    # Create an integrator object:  use "vode" with "backward 
    # differentitaion formula" for stiff ODEs
    r = integrate.ode(derivs).set_integrator('vode', method='bdf', atol=1.e-6,
        rtol=1e-3, order=5, max_step=dt_max)
    
    # Push the initial state space to the integrator object
    r.set_initial_value(q0, t0)
    
    # Make a copy of the q1_local object needed to evaluate the entrainment
    q1_local = deepcopy(q0_local)
    q0_hold = deepcopy(q1_local)
    
    # Create vectors (using the list data type) to store the solution
    t = [t0]
    q = [q0]
    
    # Integrate a finite number of time steps
    k = 0
    psteps = 30.
    stop = False
    while r.successful() and not stop:
        
        # Print progress to the screen
        if np.remainder(np.float(k), psteps) == 0.:
            print '    Distance:  %g (m), time:  %g (s), k:  %d' % \
                (q[-1][10], t[-1], k)
        
        # Perform one step of the integration
        r.set_f_params(q0_local, q1_local, profile, p, particles)
        r.integrate(t[-1] + dt_max, step=True)
        
        # Correct the temperature
        r = correct_temperature(r, q1_local, profile, p, particles)
        
        # Remove particle solution for particles outside the plume
        r = correct_particle_tracking(r, particles)
        
        # Store the results
        t.append(r.t)
        q.append(r.y)
        
        # Update the Lagrangian elements for the next time step
        q0_local = q0_hold
        q1_local.update(t[-1], q[-1], profile, p, particles)
        q0_hold = deepcopy(q1_local)
        
        # Evaluate the stop criteria
        if q[-1][10] / q1_local.D > sd_max:
            # Progressed desired distance along the plume centerline
            stop = True
        if k >= 50000:
            # Stop after specified number of iterations; used to protect 
            # against problems with the solution become stuck
            stop = True
        if q[-1][9] <= 0.:
            # Reached a location at or above the free surface
            stop = True
        if q[-1][10] == q[-2][10]:
            # Progress of motion of the plume has stopped
            stop = True
        
        # Update the index counter
        k += 1
    
    # Convert solution to numpy arrays
    t = np.array(t)
    q = np.array(q)
    
    # Show user the final calculated point and return the solution
    print '    Distance:  %g (m), time:  %g (s), k:  %d' % \
                (q[-1,10], t[-1], k)
    return (t, q)


def correct_temperature(r, q_local, profile, p, particles):
    """
    Make sure the correct temperature is stored in the state space solution
    
    When the dispersed phase particles equilibrate to their surrounding
    temperature, heat transfer is turned off by the methods in 
    `dispersed_phases.Particle`.  This is needed to prevent numerical 
    oscillation as the particle becomes small.  Unfortunately, it is not as
    easy to make the numerical solution compute the correct result once
    particle temperature effectively stops being a state space variable since
    the state space is intrinsic to the ODE solver.  The derivatives function
    computes the correct heat transfer based on the correct state space, but
    the state space in the ODE solver remains fixed.
    
    Since the solution for heat in the state space of the ODE solver is the
    wrong value, we have to change the external version of the state space
    before saving the solution to the current model step.  This follows the
    same method and reasoning as the similar function in 
    `smp.correct_temperature`.
    
    Hence, the purpose of this function is to overwrite the state space 
    solution containing the particle heat that is extrinsic to the ODE solver
    and which is used to store the state space following each time step.
    The allows the correct temperature to be stored in the model solution.
    
    Parameters
    ----------
    r : `scipy.integrate.ode` object
        ODE solution containing the current values of the state space in 
        the solver's extrinsic data.  These values are editable, but an 
        intrinsic version of these data are used when the solver makes 
        calculations; hence, editing this file does not change the state
        space stored in the actual solver.
    tp : ndarray
        Array of current particle ages, one entry for each particle in the 
        `particles` list.
    sp : ndarray
        Array of current particle positions, one entry for each particle in
        the `particles` list.  The array is organized as (x0, y0, z0, x1, 
        y1, z1, ... xn, yn, zn) for n particles.
    q_local : `bent_plume_model.LagElement`
        Object containing the numerical solution at the initial condition
    profile : `ambient.Profile` object
        The ambient CTD object used by the simulation.
    p : `ModelParams` object
        Object containing the fixed model parameters for the bent
        plume model.
    particles : list of `Particle` objects
        List of `bent_plume_model.Particle` objects containing the dispersed 
        phase local conditions and behavior.
    
    Returns
    -------
    r : `sciply.integrate.ode` object
        The updated extrinsic state space with the correct values for heat
        as were used in the calcualtion.
    
    """
    # Update the Lagrangian element state space with the current solution.
    # This will check whether heat transfer is turned off and will return 
    # the value of the particle temperature that was used in the calculation
    # step.
    q_local.update(r.t, r.y, profile, p, particles)
    
    # Find the heat conservation equation in the model state space for the 
    # particles and replace r.y with the correct values.
    idx = 11
    for i in range(len(particles)):
        idx += particles[i].particle.nc
        r.y[idx] = np.sum(particles[i].m) * particles[i].nbe * \
                       particles[i].cp * particles[i].T
        # Advance for heat, time, and position
        idx += 1 + 1 + 3
    
    # Return the corrected solution
    return r


def correct_particle_tracking(r, particles):
    """
    Remove the particle tracking solution after particles exit plume
    
    Even though the particle tracking stops as needed once the particles
    leave the plume, the post processing algorithm has now way to know if a
    given state space solution is before or after particle tracking has
    stopped. This function simply replaces the particle position after
    integration has stopped (e.g., after the particles leave the plume) with
    NaN so that the post-processor always knows whether the solution in the
    state space is valid or not.  This is necessary since the solution for 
    particle position is in local plume coordinates (l,n,m); hence, it is not
    possible to know the (x,y,z) position unless the correct local plume 
    element is known.  This function makes sure that every valid (l,n,m) is 
    stored with the corresponding element.  
    
    Parameters
    ----------
    r : `scipy.integrate.ode` object
        ODE solution containing the current values of the state space in 
        the solver's extrinsic data.  These values are editable, but an 
        intrinsic version of these data are used when the solver makes 
        calculations; hence, editing this file does not change the state
        space stored in the actual solver.
    particles : list of `Particle` objects
        List of `bent_plume_model.Particle` objects containing the dispersed 
        phase local conditions and behavior.
    
    Returns
    -------
    r : `sciply.integrate.ode` object
        The updated extrinsic state space with the correct values for heat
        as were used in the calcualtion.
    
    """
    # Check each particle to determine whether they are inside or outside
    # the plume
    
    for i in range(len(particles)):
        if not particles[i].integrate:
            # This particle is outside the plume...find the particle position 
            # in the state space solution
            idx = 11
            
            for i in range(len(particles)):
                # Skip the particle mass, heat, and time (time is always 
                # computed properly)
                idx += particles[i].particle.nc + 3
                
                # Replace the (l,n,m) coordinates with np.nan
                r.y[idx:idx+3] = np.nan
                
                # Skip forward in the state space solution to the start of 
                # the next particle solution
                idx += 3
    
    # Return the corrected solution
    return r


def entrainment(q0_local, q1_local, p):
    """
    Compute the total shear and forced entrainment at one time step
    
    Computes the local entrainment (kg/s) as a combination of shear
    entrainment and forced entrainment for a local Lagrangian element. This
    function follows the approach in Lee and Cheung (1990) to compute both
    types of entrainment, but uses the formulation in Jirka (2004) for the
    shear entrainment term. Like Lee and Cheung (1990), it uses the maximum
    entrainment hypothesis: entrainment = max (shear, forced), with the
    exception that a pure coflowing momentum jet has entrainment = shear +
    forced. This function also makes one correction that in pure coflow
    the forced entrainment should be computed by integrating around the entire
    jet, and not just the half of the jet exposed to the current.
    
    Parameters
    ----------
    q0_local : `bent_plume_model.LagElement`
        Object containing the numerical solution at the previous time step
    q1_local : `bent_plume_model.LagElement`
        Object containing the numerical solution at the current time step
    p : `ModelParams` object
        Object containing the fixed model parameters for the bent
        plume model.
    
    Returns
    -------
    md : float
        Total entrainment (kg/s)
    
    Notes
    -----
    The entrainment computed here is already integrated over the current 
    Lagrangian element surface area.  Hence, the result is (kg/s) into the 
    element.  
    
    """
    # Get the shear entrainment coefficient for the top-hat model    
    alpha_s = dispersed_phases.shear_entrainment(q1_local.V, q1_local.ua, 
        q1_local.rho, q1_local.rho_a, q1_local.b, q1_local.sin_p, 
        q1_local.cos_p, q1_local.cos_t, p)
    
    # Total shear entrainment (kg/s)
    md_s = q1_local.rho_a * np.abs(q1_local.V - q1_local.ua * 
           q1_local.cos_p * q1_local.cos_t) * alpha_s * ( 2. * np.pi * 
           q1_local.b * q1_local.h)
         
    # Compute the projected area entrainment terms...first, the crossflow 
    # projected onto the plume centerline
    a1 = 2. * q1_local.b * np.sqrt(q1_local.sin_p**2 + q1_local.sin_t**2 - 
         q1_local.sin_p**2 * q1_local.sin_t**2) * q1_local.h
    if (q1_local.s - q0_local.s) / q1_local.b <= 1.e-9:
        # The plume is not progressing along the centerline; assume the
        # expansion and curvature corrections are small since delta s / b is
        # very small.
        a2 = 0.
        a3 = 0.
    else:
        # Second, correction for plume expansion
        a2 = np.pi * q1_local.b * (q1_local.b - q0_local.b) / (q1_local.s - 
             q0_local.s) * q1_local.h * q1_local.cos_p * q1_local.cos_t
        # Third, correction for plume curvature
        a3 = np.pi * q1_local.b**2 / 2. * (q1_local.cos_p * 
             q1_local.cos_t - q0_local.cos_p * q0_local.cos_t) / (q1_local.s 
             - q0_local.s) * q1_local.h
    
    # Get the total projected area for the forced entraiment
    if np.abs(q1_local.v) <= 1.e-9 and np.abs(q1_local.w) <= 1.e-9:
        # Jet is in co-flow, shear entrainment model takes care of this case
        # by itself
        A = 0.
    else:
        A = a1 + a2 + a3
    
    # Total forced entrainment (kg/s)
    md_f = q1_local.rho_a * q1_local.ua * A
    
    # Obtain the total entrainment using the maximum hypothesis from Lee and 
    # Cheung (1990)
    if md_s > md_f:
        md = md_s
    else:
        md = md_f
    
    # Return the entrainment rate
    return md


def track_particles(q0_local, q1_local, md, particles):
    """
    Compute the forcing variables needed to track particles
    
    The independent variable in the Lagrangian plume model is t for advection
    of the continuous phase.  Because the particles slip through the fluid, 
    their advection speed is different; hence, all particle equations have 
    a different independent variable for their time, tp.  Also, particle 
    motion is computed in the local plume coordinates space (l,n,m); thus,
    the vertical slip velocity needs to be transformed to the local plume
    coordinate system.  Finally, the entrainment velocity pointing toward
    the plume centerline needs to be evaluated, based on the distance the 
    particle is from the plume centerline.
    
    Parameters
    ----------
    q0_local : `bent_plume_model.LagElement`
        Object containing the numerical solution at the previous time step
    q1_local : `bent_plume_model.LagElement`
        Object containing the numerical solution at the current time step
    md : float
        Total entrainment into the Lagrangian element (kg)
    particles : list of `Particle` objects
        List of `bent_plume_model.Particle` objects containing the dispersed 
        phase local conditions and behavior.
    
    Returns
    -------
    fe : float
        Entrainment frequency (1/s)
    up : ndarray
        Slip velocity for each particle projected on the local plume 
        coordinate system (l,n,m) (m/s).  Each row is for a different 
        particle and the columns are for the velocity in the (l,n,m)
        direction.
    dtp_dt : ndarray
        Differential particle transport time to continuous phase transport
        time evaluated from the previous time step to the current time 
        step (--).
    
    """
    # Compute the entrainment frequency
    fe = md / (2. * np.pi * q1_local.rho_a * q1_local.b**2 * 
         q1_local.h)
    
    # Get the rotation matrix to the local coordinate system (l,n,m)
    ds = q1_local.s - q0_local.s
    A = local_coords(q0_local, q1_local, ds)
    
    # Get the velocity of the current plume slice
    V = q1_local.V
    
    # Compute particle properties
    up = np.zeros((len(particles),3))
    dtp_dt = np.zeros(len(particles))
    
    for i in range(len(particles)):
        # Transform the slip velocity from Cartesian coordinate to the 
        # local plume coordinate system (l,n,m)
        up[i,:] = np.dot(A, np.array([0., 0., -particles[i].us]))
        
        # Get the distance along the particle path
        dsp = np.sqrt((q1_local.x_p[i,0] - q0_local.x_p[i,0])**2 + 
                      (q1_local.x_p[i,1] - q0_local.x_p[i,1])**2 + 
                      (q1_local.x_p[i,2] - q0_local.x_p[i,2])**2)
        
        # Get the total velocity of the particle
        Vp = np.sqrt((up[i,0] + V)**2 + 
                     (up[i,1] - fe * q1_local.X_p[i,1])**2 + 
                     (up[i,2] - fe * q1_local.X_p[i,2])**2)
        
        # Compute the particle time correction dtp/dt
        if Vp == 0:
            dtp_dt[i] = 0.
        elif ds == 0:
            dtp_dt[i] = 1.
        else:
            dtp_dt[i] = V / Vp * dsp / ds
    
    # Return the particle tracking variables
    return (fe, up, dtp_dt)


def local_coords(q0_local, q1_local, ds):
    """
    Compute the rotation matrix from (x, y, z) to (l, n, m)
    
    Computes the rotation matrix from the local Cartesian coordinate system
    (x - xi, y - yi, z - zi), where (xi, yi, zi) is the current location of
    the Lagrangian plume element, to the system tangent to the current plume
    trajectory (l, n, m); l is oriented tangent to the plume centerline, 
    n is orthogonal to l and along the line from the local radius of 
    curvature, and m is orthogonal to n and l.  The transformation is 
    provided in Lee and Chueng (1990).  This method makes a small adaptation
    allowing for infinite radius of curvature (plume propagating along a 
    straight line).  
    
    Parameters
    ----------
    q0_local : `bent_plume_model.LagElement`
        Object containing the numerical solution at the previous time step
    q1_local : `bent_plume_model.LagElement`
        Object containing the numerical solution at the current time step
    ds : float
        Segment length along the centerline between the solutions contained 
        in `q0_local` and `q1_local`.
    
    Returns
    -------
    A : ndarray
        Rotation matrix from (x, y, z)' to (l, n, m).  The inverse of this
        matrix will convert back from (l, n, m) to (x, y, z)'.
    
    See Also
    --------
    bent_plume_model.Particle.track
    
    """
    # Get the rate of angular rotation for the centerline
    if ds < 1.e-12:
        phi_d = 0.
        theta_d = 0.
    else:
        phi_d = (q1_local.phi - q0_local.phi) / ds
        if np.abs(q1_local.theta - q0_local.theta) > np.pi:
            # Angles are close to 0
            if q1_local.theta > np.pi:
                theta_d = (q1_local.theta - (q0_local.theta + 2. * np.pi)) / ds
            else:
                theta_d = (q1_local.theta + 2.* np.pi - q0_local.theta) / ds
        else:
            theta_d = (q1_local.theta - q0_local.theta) / ds
    
    # Get the value of 1 / R = r
    r = np.sqrt(phi_d**2 + q1_local.cos_p**2 * theta_d**2)
    
    # Compute the rotation matrix between (i,j,k) and (l,n,m)
    if r < 1.e-8:
        # Trajectory is straight and R is infinite
        A = np.array([
            [q1_local.cos_p * q1_local.cos_t, 
             q1_local.cos_p * q1_local.sin_t, 
             q1_local.sin_p], 
            [q1_local.cos_t * q1_local.sin_p,
             q1_local.sin_t * q1_local.sin_p,
             -q1_local.cos_p], 
            [q1_local.sin_t,
             -q1_local.cos_t,
             0.]])
    else:
        # Trajectory is curving, and R is finite
        A = np.array([
            [q1_local.cos_p * q1_local.cos_t, 
             q1_local.cos_p * q1_local.sin_t, 
             q1_local.sin_p], 
            [q1_local.cos_p * q1_local.sin_t * theta_d + q1_local.cos_t * 
                q1_local.sin_p * phi_d, 
             q1_local.sin_t * q1_local.sin_p * phi_d - q1_local.cos_p * 
                q1_local.cos_t * theta_d, 
             -q1_local.cos_p * phi_d] / r, 
            [q1_local.sin_t * phi_d - q1_local.sin_p * q1_local.cos_p * 
                q1_local.cos_t * theta_d,
             -q1_local.cos_t * phi_d - q1_local.cos_p * q1_local.sin_t * 
             q1_local.sin_p * theta_d, 
             q1_local.cos_p**2 * theta_d] / r])
    
    # Return the rotation matrix
    return A


# ----------------------------------------------------------------------------
# Functions to compute the initial conditions for the first model element
# ----------------------------------------------------------------------------

def main_ic(profile, particles, X, D, Vj, phi_0, theta_0, Sj, Tj, cj, 
            tracers, p):
    """
    Compute the initial conditions for the Lagrangian plume state space
    
    Compute the initial conditions at the release location for a Lagrangian
    plume element.  This can either be a pure single-phase plume, a pure
    multiphase plume, or a mixed release of multiphase and continuous phase
    fluid.
    
    Parameters
    ----------
    profile : `ambient.Profile` object
        The ambient CTD object used by the single bubble model simulation.
    particles : list of `Particle` objects
        List of `bent_plume_model.Particle` objects containing the dispersed 
        phase local conditions and behavior.
    X : ndarray
        Release location (x, y, z) in (m)
    D : float
        Diameter for the equivalent circular cross-section of the release 
        (m)
    Vj : float
        Scalar value of the magnitude of the discharge velocity for 
        continuous phase fluid in the discharge.  This variable should be 
        0 or None for a pure multiphase discharge.
    phi_0 : float
        Vertical angle from the horizontal for the discharge orientation 
        (rad in range +/- pi/2)
    theta_0 : float
        Horizontal angle from the x-axis for the discharge orientation.  
        The x-axis is taken in the direction of the ambient current.  
        (rad in range 0 to 2 pi)
    Sj : float
        Salinity of the continuous phase fluid in the discharge (psu)
    Tj : float
        Temperature of the continuous phase fluid in the discharge (T)
    cj : ndarray
        Concentration of passive tracers in the discharge (user-defined)
    tracers : string list
        List of passive tracers in the discharge.  These can be chemicals 
        present in the ambient `profile` data, and if so, entrainment of 
        these chemicals will change the concentrations computed for these 
        tracers.  However, none of these concentrations are used in the 
        dissolution of the dispersed phase.  Hence, `tracers` should not 
        contain any chemicals present in the dispersed phase particles.
    p : `stratified_plume_model.ModelParams` object
        Object containing the fixed model parameters for the stratified 
        plume model.
    
    Returns
    -------
    t : float
        Initial time for the simulation (s)
    q : ndarray
        Initial value of the plume state space
    chem_names : str list
        List of the chemicals in the dispersed phase composition that are 
        undergoing dissolution
    
    """
    # Get the initial volume flux
    if Vj is None or Vj == 0.:
        # This is a pure multiphase plume.  Estimate the initial conditions 
        # using Wuest et al. 1992.
        Q, A, X, Tj, Sj, Pj, rho_j = dispersed_phases.zfe_volume_flux(profile, 
            particles, p, X, D/2.)
    
    else:
        # The discharge contains continuous phase fluid.  Get the flow rate
        # from the discharge conditions.
        Q, A, X, Tj, Sj, Pj, rho_j = zfe_volume_flux(profile, X, D/2., Vj, 
                                     Sj, Tj)
    
    # Get the names of the chemicals to track
    chem_names = dispersed_phases.get_chem_names(particles)
    
    # Build the initial state space with these initial values
    t, q = bent_plume_ic(profile, particles, Q, A, D, X, phi_0, theta_0, 
             Tj, Sj, Pj, rho_j, cj, chem_names, tracers, p)
    
    # Return the initial state space
    return (t, q, chem_names)


def bent_plume_ic(profile, particles, Qj, A, D, X, phi_0, theta_0, Tj, Sj, 
                  Pj, rho_j, cj, chem_names, tracers, p):
    """
    Build the Lagragian plume state space given the initial conditions
    
    Constructs the initial state space for a Lagrangian plume element from 
    the initial values for the base plume variables (e.g., Q, J, u, S, T, 
    etc.).
    
    Parameters
    ----------
    profile : `ambient.Profile` object
        The ambient CTD object used by the single bubble model simulation.
    particles : list of `Particle` objects
        List of `bent_plume_model.Particle` objects containing the dispersed 
        phase local conditions and behavior.
    Qj : Volume flux of continuous phase fluid at the discharge (m^3/s)
    A : Cross-sectional area of the discharge (M^2)
    D : float
        Diameter for the equivalent circular cross-section of the release 
        (m)
    X : ndarray
        Release location (x, y, z) in (m)
    phi_0 : float
        Vertical angle from the horizontal for the discharge orientation 
        (rad in range +/- pi/2)
    theta_0 : float
        Horizontal angle from the x-axis for the discharge orientation.  
        The x-axis is taken in the direction of the ambient current.  
        (rad in range 0 to 2 pi)
    Tj : float
        Temperature of the continuous phase fluid in the discharge (T)
    Sj : float
        Salinity of the continuous phase fluid in the discharge (psu)
    Pj : float
        Pressure at the discharge (Pa)
    rho_j : float
        Density of the continous phase fluid in the discharge (kg/m^3)
    cj : ndarray
        Concentration of passive tracers in the discharge (user-defined)
    chem_names : string list
        List of chemical parameters to track for the dissolution.  Only the 
        parameters in this list will be used to set background concentration
        for the dissolution, and the concentrations of these parameters are 
        computed separately from those listed in `tracers` or inputed from
        the discharge through `cj`.
    tracers : string list
        List of passive tracers in the discharge.  These can be chemicals 
        present in the ambient `profile` data, and if so, entrainment of 
        these chemicals will change the concentrations computed for these 
        tracers.  However, none of these concentrations are used in the 
        dissolution of the dispersed phase.  Hence, `tracers` should not 
        contain any chemicals present in the dispersed phase particles.
    p : `stratified_plume_model.ModelParams` object
        Object containing the fixed model parameters for the stratified 
        plume model.
    
    Returns
    -------
    t : float
        Initial time for the simulation (s)
    q : ndarray
        Initial value of the plume state space
    
    """
    
    # Set the dimensions of the initial Lagrangian plume element.
    b = D / 2.
    h = D / 5.
    
    # Measure the arc length along the plume
    s0 = 0.
    
    # The total discharge volume flux is the jet discharge since we assume
    # the void fraction of gas is negligible
    Q = Qj
    
    # Determine the time to fill the initial Lagrangian element
    dt = np.pi * b**2 * h / Q
    
    # Compute the mass of jet discharge in the initial Lagrangian element
    Mj = Qj * dt * rho_j
    
    # Evaluate the mass of particles in the intial Lagrangian element.  Since
    # particles are tracked by number and mass per particle, we need to know
    # how many particles enter the Lagrangian element.  This should be the 
    # number flux in #/s time the fill time for the Lagrangian element, dt.
    # Store this value in the `Particle` objects for use throughout the model.
    nbe = np.zeros(len(particles))
    for i in range(len(particles)):
        nbe[i] = particles[i].nb0 * dt
        particles[i].nbe = nbe[i]
    
    # Get the velocity in the component directions
    Uj = flux_to_velocity(Qj, A, phi_0, theta_0)
    
    # Compute the magnitude of the exit velocity 
    V = np.sqrt(Uj[0]**2 + Uj[1]**2 + Uj[2]**2)
    
    # Build the continuous-phase portion of the model state space vector
    t = 0.
    q = [Mj, Mj * Sj, Mj * seawater.cp() * Tj, Mj * Uj[0], Mj * Uj[1], 
          Mj * Uj[2], h / V, X[0], X[1], X[2], s0]
    
    # Add in the state space for the dispersed phase particles
    q.extend(dispersed_phases.particles_state_space(particles, nbe))
    
    # Add the ambient concentrations of the dispersed-phase chemicals
    ca = profile.get_values(X[2], chem_names)
    q.extend(Mj / rho_j * ca)
    
    # Add in the tracers discharged with the jet
    q.extend(Mj*cj)
    
    # Return the complete initial conditions
    return (t, np.array(q))


def zfe_volume_flux(profile, X0, R, Vj, Sj, Tj):
    """
    Compute the volume flux of continous phase discharge fluid at the release
    
    If the release includes continous phase fluid, this function computes
    the flow rate and geometry of the release
    
    Parameters
    ----------
    profile : `ambient.Profile` object
        The ambient CTD object used by the single bubble model simulation.
    X0 : ndarray
        Release location (x, y, z) in (m)
    R : float
        Radius for the equivalent circular cross-section of the release 
        (m)
    Vj : float
        Scalar value of the magnitude of the discharge velocity for 
        continuous phase fluid in the discharge.  This variable should be 
        0 or None for a pure multiphase discharge.
    Sj : float
        Salinity of the continuous phase fluid in the discharge (psu)
    Tj : float
        Temperature of the continuous phase fluid in the discharge (T)
    
    Returns
    -------
    Q : Volume flux of continuous phase fluid at the discharge (m^3/s)
    A : Cross-sectional area of the discharge (M^2)
    X : ndarray
        Release location (x, y, z) in (m)
    Tj : float
        Temperature of the continuous phase fluid in the discharge (T)
    Sj : float
        Salinity of the continuous phase fluid in the discharge (psu)
    Pj : float
        Pressure at the discharge (Pa)
    rho_j : float
        Density of the continous phase fluid in the discharge (kg/m^3)
    
    """
    # The Lagrangian plume model starts at the discharge.
    X = X0
    
    # Get the jet density from the discharge characteristics
    Ta, Sa, P = profile.get_values(X[2], ['temperature', 'salinity', 
        'pressure'])
    rho_j = seawater.density(Ta, Sa, P)
    
    # Pressure at the discharge is the ambient pressure
    Pj = P
    
    # The discharge area if the full port area
    A = np.pi * R**2
    
    # Compute the volume flux of discharge fluid
    Q = A * Vj
    
    # Return the initial conditions with salinity and temperature of the 
    # discharge equal to the jet values
    return (Q, A, X, Tj, Sj, Pj, rho_j)


def flux_to_velocity(Q, A, phi, theta):
    """
    Convert fluid flow rate to three-component velocity 
    
    Computes the three-component velocity (u, v, w) along the Cartesian 
    directions (x, y, depth) from the flow rate, cross-sectional area, and the
    orientation (phi and theta).
    
    Parameters
    ----------
    Q : Volume flux of continuous phase fluid at the discharge (m^3/s)
    A : Cross-sectional area of the discharge (M^2)
    phi : float
        Vertical angle from the horizontal for the discharge orientation 
        (rad in range +/- pi/2)
    theta : float
        Horizontal angle from the x-axis for the discharge orientation.  
        The x-axis is taken in the direction of the ambient current.  
        (rad in range 0 to 2 pi)
    
    Returns
    -------
    Uj : ndarray
        Vector of the three-component velocity of continous phase fluid in 
        the jet (u, v, w) in the Cartesian direction (x, y, depth) 
    
    """
    # Get the velocity along the jet centerline
    Vj = Q / A
    
    # Project jet velocity on the three component directions (i, j, k)
    Uj = np.zeros(3)
    Uj[0] = np.cos(phi) * np.cos(theta) * Vj
    Uj[1] = np.cos(phi) * np.sin(theta) * Vj
    Uj[2] = np.sin(phi) * Vj
    
    # Return the velocity vector
    return Uj

