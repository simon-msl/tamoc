"""
Sintef
======

Evaluate initial bubble and droplet sizes from the SINTEF model

This module applies the modified Weber number model of SINTEF with corrections
for the presence of oil and gas together to estimate the volume mean diameters
of oil and gas at the end of the break-up region.

Notes 
----- 
The empirical coefficients A and B were obtained from Tower Basin experiments
of oil into water with and without dispersant.  These experiments had small
exit diameters (generally 1.5 mm or less) and large relative momentum length
scales l_M/D >> 1.  The corrections for the presence of gas in oil (both a 
void fraction corrections and buoyancy correction) are theoretical, and any
affect these corrections may have on the parameters A and B has not yet been
studied in detail.  

References
----------
Brandvik, P.J., Johansen, O., Farooq, U., Angell, G., and Leirvik, F. (2012),
"Sub-surface oil releases--Experimental study of droplet distributions and 
different dispersant injection techniques.  A scaled experimental approach
using the SINTEF Tower Basin."  Draft report, prepared by SINTEF Materials
and Chemistry, Aug. 2012.

Johansen, O., Brandvik, P.J. and Farooq, U. (2012), "Droplet breakup in subsea
oil releases--Part 2: Prediction of droplet size distributions with and 
without injection of chemical dispersants."  Marine Pollution Bulletin. 

"""
# S. Socolofsky, September 2013, Texas A&M University <socolofs@tamu.edu>.

import numpy as np
from copy import copy
from scipy.optimize import fsolve

def modified_We_model(D, rho_gas, m_gas, mu_gas, sigma_gas, rho_oil, m_oil, 
                      mu_oil, sigma_oil, rho):
    """
    Compute the initial oil droplet and gas bubble sizes from the SINTEF model
    
    Apply the SINTEF modified Weber number model to estimate the initial 
    oil and gas particle sizes.  This function calculates the adjusted exit
    velocity based on a void fraction and buoyancy adjustment per the method
    suggested by SINTEF.
    
    Parameters
    ----------
    D : float
        Diameter of the release point (m)
    rho_gas : float
        In-situ density of the gas (kg/m^3)
    m_gas : ndarray
        Array of mass fluxes for each component of the gas object (kg/s)
    mu_gas : float
        Dynamic viscosity of gas (Pa s)
    sigma_gas : float
        Interfacial tension between gas and seawater (N/m)
    rho_oil : float
        In-situ density of the oil
    m_oil : ndarray
        Array of mass fluxes for each component of the oil object (kg/s)
    mu_oil : float
        Dynamic viscosity of oil (Pa s)
    sigma_oil : float
        Interfacial tension between oil and seawater (N/m)
    rho : float
        Density of the continuous phase fluid (kg/m^3)
    
    Returns
    -------
    A tuple containing:
        de_gas : float
            The volume mean diameter of the gas bubbles (m)
        de_oil : float
            The volume mean diameter of the oil droplets (m)
    """
    # Make sure the masses are in arrays
    if not isinstance(m_gas, np.ndarray):
        if not isinstance(m_gas, list):
            m_gas = np.array([m_gas])
        else:
            m_gas = np.array(m_gas)
    if not isinstance(m_oil, np.ndarray):
        if not isinstance(m_oil, list):
            m_oil = np.array([m_oil])
        else:
            m_oil = np.array(m_oil)
    
    # Get the volume flow rates of gas and oil
    if np.sum(m_gas) >0.:
        Q_gas = np.sum(m_gas) / rho_gas
    else:
        Q_gas = 0.
    if np.sum(m_oil) >0.:
        Q_oil = np.sum(m_oil) / rho_oil
    else:
        Q_oil = 0.
        
    # Get the void-fraction adjusted velocity Un
    if Q_oil == 0.:
        # This is gas only
        n = 1.0
        Un = 4. * Q_gas / (np.pi * D**2)
    else:
        # This is either oil only or oil and gas
        n = Q_gas / (Q_gas + Q_oil)
        Un = 4. * Q_oil / (np.pi * D**2) / (1. - n)**(1./2.)
    
    # Get the densimetric Froude number
    Fr = Un / (9.81 * (rho - rho_oil * (1. - n) - rho_gas * n) / \
         rho * D)**(1./2.)
    
    # Compute the final characteristic velocity to use in the model
    Uc = Un * (1. + 1./Fr)
    
    # Compute the gas volume mean droplet size
    if Q_gas > 0.:
        de_gas = de_50(Uc, D, rho_gas, mu_gas, sigma_gas, rho)
    else:
        de_gas = None
    
    # Compute the oil volume mean droplet size
    if Q_oil > 0.:
        de_oil = de_50(Uc, D, rho_oil, mu_oil, sigma_oil, rho)
    else:
        de_oil = None
    
    # Return the bubble and droplet sizes
    return (de_gas, de_oil)


# Provide tool to estimate the maximum stable particle size
def de_max(sigma, rho_p, rho):
    """
    Calculate the maximum stable particle size
    
    Predicts the maximum stable particle size per Clift et al. (1978) via 
    the equation:
    
    d_max = 4. * np.sqrt(sigma / (g (rho - rho_p)))
    
    Parameters
    ----------
    sigma : float
        Interfacial tension between the phase undergoing breakup and the 
        ambient receiving continuous phase (N/m)
    rho_p : float
        Density of the phase undergoing breakup (kg/m^3)
    rho : float
        Density of the ambient receiving continuous phase (kg/m^3) 
    
    Returns
    -------
    d_max : float
        Maximum stable particle size (m)
    
    """
    return 4. * np.sqrt(sigma / (9.81 * (rho - rho_p)))

def de_50(U, D, rho_p, mu_p, sigma, rho):
    """
    Predict the volume mean diameter from a modified Weber number model
    
    Calculates the SINTEF modified Weber number model for the volume mean 
    diameter of a blowout release.
    
    Parameters
    ----------
    U : float
        Effective exit velocity after void fraction and buoyancy correction 
        of the phase undergoing breakup (m/s)
    D : float
        Diameter of the discharge (m)
    rho_p : float
        Density of the phase undergoing breakup (kg/m^3)
    mu_p : float
        Dynamic viscosity of the phase undergoing breakup (Pa s)
    sigma : float
        Interfacial tension between the phase undergoing breakup and the 
        ambient receiving continuous phase (N/m)
    
    Returns
    -------
    de_50 : float
        The volume mean diameter of the phase undergoing breakup
    
    Notes
    -----
    This function first checks the We number.  If the We is less than the 
    critical value of 350 required for atomization, then the fluid particle 
    diameter is estimated as 1.2 D.  Otherwise, the SINTEF modified We number 
    model is used.  In both cases, the resulting particle diameter is compared
    to the maximum stable particle size per Clif et al. (1978) of 
        
        d_max = 4 (sigma/ (g (rho - rho_p)))^(1/2).  
        
    The function returns the lesser of the estimated particle size or the 
    maximum stable particle size.
    
    """
    # Compute the non-dimensional constants
    We = rho_p * U**2 * D / sigma
    Vi = mu_p * U / sigma
    
    if We > 350.:
        # Atomization...use the SINTEF model.
        # A = 24.8
        # B = 0.08
        A = 15.
        B = 0.8
        
        # Solve for the volume mean diameter from the implicit equation
        def residual(dp):
            """
            Compute the residual of the SINTEF modified Weber number model
            
            Evaluate the residual of the non-dimensional diameter dp = de_50 / D
            for the SINTEF droplet break-up model.
            
            Input variables are:
                We, Vi, A, B = constant and inherited from above
                dp = Non-dimensional diameter de_50 / D (--)
            
            """
            # Compute the non-dimensional diameter and return the residual
            return dp - A * (We / (1. + B * Vi * dp**(1./3.)))**(-3./5.)
        
        # Find the gas and liquid fraction for the mixture
        dp = fsolve(residual, 5.)[0]
        de = dp * D
    else:
        # Sinuous wave breakup...use the pipe diameter
        de = 1.2 * D
    
    # Require the diameter to be less than the maximum stable size
    dmax = de_max(sigma, rho_p, rho)
    if de > dmax:
        de = dmax
    
    # Return the result
    return de

def rosin_rammler(nbins, d50, md_total, sigma, rho_p, rho):
    """
    Return the volume size distribution from the Rosin Rammler distribution
    
    Returns the fluid particle diameters in the selected number of bins on
    a volume basis from the Rosin Rammler distribution with parameters 
    k = -ln(0.5) and alpha = 1.8.  
    
    Parameters
    ----------
    nbins : int
        Desired number of size bins in the particle volume size distribution
    d50 : float
        Volume mean particle diameter (m)
    md_total : float
        Total particle mass flux (kg/s)
    sigma : float
        Interfacial tension between the phase undergoing breakup and the 
        ambient receiving continuous phase (N/m)
    rho_p : float
        Density of the phase undergoing breakup (kg/m^3)
    rho : float
        Density of the ambient receiving continuous phase (kg/m^3) 
    
    Returns
    -------
    de : ndarray
        Array of particle sizes at the center of each bin in the distribution
        (m)
    md : ndarray
        Total mass flux of particles corresponding to each bin (kg/s)
    
    Notes
    -----
    This method computes the un-truncated volume size distribution from the
    Rosin-Rammler distribution and then enforces that all particle sizes
    be less than the maximum stable size by moving mass in larger sizes to 
    the maximum stable size bin.  
    
    References
    ----------
    Johansen, Brandvik, and Farooq (2013), "Droplet breakup in subsea oil
    releases - Part 2: Predictions of droplet size distributions with and 
    without injection of chemical dispersants." Marine Pollution Bulletin,
    73: 327-335.  doi:10.1016/j.marpolbul.2013.04.012.
    
    """
    # Compute the maximum stable particle diameter
    dmax = de_max(sigma, rho_p, rho)
    
    # Define the parameters of the distribution
    k = -np.log(0.5)
    alpha = 1.8
    
    # Get the de/d50 ratio for the edges of each bin in the distribution 
    # using a log-spacing
    bin_edges = np.logspace(-1, 1, nbins + 1)
    
    # Find the logarithmic average location of the center of each bin
    bin_centers = np.zeros(len(bin_edges) - 1)
    
    for i in range(len(bin_centers)):
        bin_centers[i] = np.exp(np.log(bin_edges[i]) + 
                         (np.log(bin_edges[i+1]) - np.log(bin_edges[i])) / 2.)
    
    # Get the cumulative volume fraction within each bin
    Vn = 1. - np.exp(-k * bin_edges**alpha)
    
    # Get the actual volume fraction within each bin
    V_frac = np.zeros(len(bin_centers))
    for i in range(len(bin_edges) - 1):
        V_frac[i] = Vn[i+1] - Vn[i]
    V_frac += (1.0 - np.sum(V_frac)) / nbins
    
    # Compute the actual diameters of each particle
    de = d50 * bin_centers
    
    # Compute the mass fraction for each diameter
    md = V_frac * md_total
    
    # Truncate the distribution at the maximum stable droplet size
    imax = -1
    for i in range(len(de)):
        if de[i] > dmax:
            if imax < 0:
                imax = i
                de[i] = dmax
            else:
                md[imax] += md[i]
                md[i] = 0.
    
    # Return the particle size distribution
    return (de, md)

