
import numpy as np
import numpy.matlib as mb
import fcgadgets.utilities.utilities_general as gu

'''============================================================================

POTENTIAL EVAPOTRANSPIRATION

Input variables:
    Time - time vector [Year,Month]
    ta - monthly mean air temperature (degC)
    rswn - monthly mean net short wave radiation (MJ/m2/d)
    u - mean monthly wind speed (m/s) (defaults to 2.0 m/s if not supplied)
    Gs - mean monthly surface conductance (m/s) (required only for Method =
    Penman-Monteith)    

Methods of calculation:
    1) Components 
    2) Equilibrium rate
    3) Priestley-Taylor (1972)
    4) Penman (1948)
    5) Penman-Moneith

============================================================================'''

def GetETp(vi,Method,TimeInterval):
    
    # Dimensions (months, locations)
    try:
        N_m,N_s=vi['rswn'].shape
    except:
        N_m=vi['rswn'].size
        N_s=1
    
    # Number of years
    N_yr=int(N_m/12)
    
    # Wind speed - if not supplied, use default of 2.0 m s-1.
    if 'u' not in vi:
        vi['u']=2.0
        
    # Day of year
    #doyv=np.array([15,46,74,105,135,166,196,227,258,288,319,349])
    #if vi['Time'].shape[0]==1:            
    #    doy=doyv[vi['Time'][:,1]]           
    #else:            
    #    doy=np.matlib.repmat(doyv,N_yr,N_s)
    
    # Constants
    con=HydroMetCon()
    
    # Convert net shortwave radiation from MJ m-2 d-1 to W m-2
    Rswn_conv=vi['rswn']*1e6/con['DayLength']
    
    # Convert net shortwave radiation (W m-2) to net radiation (W m-2)
    # Parameters from this were fitted to data at DF49 and agree roughly with 
    # Landsberg's textbook.
    Rn=con['Rswn2Rn_Slope']*Rswn_conv+con['Rswn2Rn_AddOffset']
    
    # Psychrometric term (hPa K-1)
    Psychro=0.01*GetPsychrometric(vi['ta'],'Pressure')
    
    # Saturation vapour pressure/temperature slope (hPa K-1)    
    Svps=0.01*GetSVPSlope(vi['ta'],'Pressure')
    
    if Method=='Components':
        
        # Radiative component (mm d-1) (McMahon et al. 2013)
        Eeq=((Svps/(Svps+Psychro))*Rn)*con['DayLength']/con['Lam']
    
        # Aerodynamic component (mm d-1) (McMahon et al. 2013)
        Ea=(1.31+1.38*vi['u'])*(vi['vpd']/10)
        
        # Add to tuple
        ETp=(Eeq,Ea)
        
    elif Method=='Equilibrium':
        
        # Radiative component (mm d-1) (McMahon et al. 2013)
        ETp=((Svps/(Svps+Psychro))*Rn)*con['DayLength']/con['Lam'] 
        
    elif Method=='Priestley-Taylor':
        
        # Radiative component (mm d-1) (McMahon et al. 2013)
        Eeq=((Svps/(Svps+Psychro))*Rn)*con['DayLength']/con['Lam']     
        
        # Potential evaporatnspiration (Priestley and Taylor 1972) (mm d-1)
        ETp=con['Alpha_PT']*Eeq
    
    elif Method=='Penman':
    
        # Radiative component (mm d-1) (McMahon et al. 2013)
        Eeq=((Svps/(Svps+Psychro))*Rn)*con['DayLength']/con['Lam']
    
        # Aerodynamic component (mm d-1) (McMahon et al. 2013)
        Ea=(1.31+1.38*vi['u'])*(vi['vpd']/10)
    
        # Potential evapotranspiration (mm d-1) (Penman 1948)
        ETp=Eeq+(Psychro/(Svps+Psychro))*Ea
    
    elif Method=='Penman-Monteith':
        
        # Aerodynamic conductance (m s-1)
        if 'Ga' not in vi:
            vi['Ga']=0.01*vi['u']
        
        # Potential evapotranspiration from Penman-Monteith combination model (mm d-1)
        ETp=((Svps*Rn + con['RhoAir']*con['CpAir']*vi['vpd']*vi['Ga'])/(Svps+Psychro*(1+vi['Ga']/vi['Gs'])))/con['Lam']*con['DayLength']
    
    else:
        print('Method not recognized, quiting.')
        return ()
    
    # Unit conversion
    if (TimeInterval=='Month') | (TimeInterval=='M') | (TimeInterval=='m'):
        DIM=mb.repmat(np.reshape(con['DIM'],(-1,1)),N_yr,N_s)
        ETp=ETp*DIM
    
    return ETp

'''============================================================================

TADPOLE - MONTHLY SURFACE WATER BALANCE MODEL

Inputs variables (field names in vi structure):
    
    LAI, leaf area index (m2 m-2)
    lat, latitude (deg)
    ta, mean air temperature (degC)
    prcp, total precipitation (mm month-1)
    rswn, net shortwave radiation (MJ m-2 d-1)
    vpd, vapour pressure deficit (hPa)
    Ws, water content of soil (mm) ** Optional **
    Wsp, water content of snow (mm) ** Optional **

Outputs variables (field names in ov structure):
    
    ETp, potential evapotranspiration (mm month-1)
    ETa, actual evapotranspiration (mm month-1)
    Ws, water content of soil (mm)
    Wsp, water content of snow (mm)
    M, snowmelt(mm month-1)
    R, runoff (mm month-1)
    RSF, rain to snow ratio (dim)

Mode of operation: 

If you are working with a small number of locations, use
"Combined" which runs all spatial units simultaneously. If running with large
grids, use "Grid" which inputs and outputs one month at a time. In the latter
approach, the state variables need to be initialized for the first time step,
and included as input arguments (from the output of t-1) for subsequent time 
steps.
 
Parameters required in meta structure:

    Ws_max: water storage capacity (200 mm)
    Tmin: minimum rain/snow fraction temperature (-3 degC) 
    Tmax: maximum rain/snow fraction temperature (-3 degC) 
    Daily Interval: daily soil water computaiton interval (5)

Warnings:

    1) This code will not tolerate NaNs.
    2) The model requires spin-up for at least one year.

============================================================================'''

def Tadpole(par,vi):
    
    #--------------------------------------------------------------------------
    # Set up output arguments
    #--------------------------------------------------------------------------
    
    vo={}
    
    # Size of input variables
    N_mo,N_s=vi['ta'].shape
    
    if par['Method']=='Combined':
        
        # Running all time steps for a set of n locations        
        vo['Ws']=par['Ws_max']*np.ones((N_mo,N_s))
        vo['Wsp']=np.zeros((N_mo,N_s))
        vo['ETp']=np.zeros((N_mo,N_s))
        vo['ETa']=np.zeros((N_mo,N_s))
        vo['R']=np.zeros((N_mo,N_s))
        vo['M']=np.zeros((N_mo,N_s))
        
    elif par['Method']=='Grid':
        
        # If running one month grid at a time, set initial values beased on input
        # from last run
        
        # Initialize state variables with maximum levels and add previous time 
        # step for time steps beyond the first one
        
        if 'Ws' in vi:
            vo['Ws'][0,:]=vi['Ws']
            vo['Wsp'][0,:]=vi['Wsp']
        else:
            vo['Ws']=par['Ws_max']*np.ones((1,N_mo))
            vo['Wsp']=np.zeros((1,N_mo))
         
        vo['ETp']=np.zeros((1,N_mo))
        vo['ETa']=np.zeros((1,N_mo))
        vo['R']=np.zeros((1,N_mo))
        vo['M']=np.zeros((1,N_mo))
    
    #--------------------------------------------------------------------------    
    # Constants
    #--------------------------------------------------------------------------
    
    con=HydroMetCon()
    
    #--------------------------------------------------------------------------
    # Potential evapotranspiratiom (mm month-1)
    #--------------------------------------------------------------------------
    
    vo['ETp']=GetETp(vi,par['ETp Method'],'Month')    
        
    #--------------------------------------------------------------------------    
    # Canopy interception as a function of leaf area (Landsberg et al. 2005)
    # Maximum proportion of rainfall evaporated from canopy MaxIntcptn – 0.15
    # Leaf area index for maximum rainfall interception LAImaxIntcptn – 5
    #--------------------------------------------------------------------------
    
    # Fraction of intercepted precipitation
    FracPrecipInt=par['Ei_FracMax']*np.minimum(1.0,vi['LAI']/par['Ei_ALMax'])

    # Potential evaporation of intercepted precipiration (mm month-1)
    Ei_Potential=FracPrecipInt*vi['prcp']

    # Actual evaporation of intercepted precipiration, defined as the minimum
    # between the energy-limited rate, and the water-limited rate (mm month-1)
    Ei_Actual=np.minimum(vo['ETp'],Ei_Potential)

    # Transpiration at energy-limited rate, i.e. prior to adding surface 
    # constraints (mm month-1)
    Et_EnergyLimited=vo['ETp']-Ei_Actual

    # Throughfall (mm month-1)
    P_Throughfall=vi['prcp']-Ei_Actual

    # Partition total precipitation into solid and liquid components
    fT=(vi['ta']-par['Tmin'])/(par['Tmax']-par['Tmin'])
    fT=np.minimum(np.maximum(0,fT),1)
    Pr=fT*P_Throughfall
    Ps=P_Throughfall-Pr
        
    #--------------------------------------------------------------------------    
    # Loop through months
    #--------------------------------------------------------------------------
    
    for iT in range(N_mo):
        
        #----------------------------------------------------------------------    
        # Set inititial daily water pools at level sfromt he end of the last 
        # month
        #----------------------------------------------------------------------
    
        if par['Method']=='Combined':
            
            if iT==0:
                Ws_d=vo['Ws'][iT,:]
                Wsp_d=vo['Wsp'][iT,:]
            else:
                Ws_d=vo['Ws'][iT-1,:]
                Wsp_d=vo['Wsp'][iT-1,:]
            
        elif par['Method']=='Grid':
            
            Ws_d=vo['Ws']
            Wsp_d=vo['Wsp']
            
        #----------------------------------------------------------------------    
        # Daily fluxes (mm d-1)
        #----------------------------------------------------------------------
    
        Ps_d=Ps[iT,:]/(30/par['Daily_Interval'])
        Pr_d=Pr[iT,:]/(30/par['Daily_Interval'])        
        Ei_Actual_d=Ei_Actual[iT,:]/(30/par['Daily_Interval'])
        Et_EnergyLimited_d=Et_EnergyLimited[iT,:]/(30/par['Daily_Interval'])
        
        for iDay in range(0,30,par['Daily_Interval']):
            
            # Potential snowmelt (mm d-1), equation from Thornthwaite and Mather (1955) 
            M_d=2.63+2.55*vi['ta'][iT,:]+0.0912*vi['ta'][iT,:]*Pr_d
        
            # Actual snowmelt (mm d-1)
            M_d=np.maximum(np.zeros((1,N_s)),np.minimum(M_d,Wsp_d+Ps_d))
    
            # Cumulative snowmelt (mm)    
            vo['M'][iT,:]=vo['M'][iT,:]+M_d 
    
            # Updated snowpack water content (mm)
            Wsp_d=Wsp_d+Ps_d-M_d 
    
            # Update soil water content (mm)
            Ws_d=Ws_d+M_d+Pr_d
    
            # Supply function (Willmott et al. 1985)
            # x=[0:0.1:1]' plot(x,1-exp(-6.68*(x)),'ko')
            fWs=np.minimum(1,np.maximum(0,1-np.exp(-6.68*(Ws_d/par['Ws_max']))))
        
            # Actual evaporation (mm d-1)
            Et_Actual_d=fWs*Et_EnergyLimited_d
            
            # Cumulative actual evapotranspiration as the sum of wet-canopy 
            # evaporation and transpiration (mm)    
            vo['ETa'][iT,:]=vo['ETa'][iT,:]+Ei_Actual_d+Et_Actual_d 
    
            # Remove transpiration from soil water pool (mm)
            Ws_d=Ws_d-Et_Actual_d
    
            # Find any spatial units where soil water exceeds capacity and add 
            # "surplus" to monthly runoff and restrict soil water content 
            # to capacity
            R_d=np.maximum(0,Ws_d-par['Ws_max'])     
            Ws_d=np.minimum(Ws_d,par['Ws_max'])
                
            # Update monthly runoff (mm)
            vo['R'][iT,:]=vo['R'][iT,:]+R_d
            
        # Update water pools
        vo['Ws'][iT,:]=Ws_d
        vo['Wsp'][iT,:]=Wsp_d
       
    #--------------------------------------------------------------------------    
    # Constrain pools to be positve
    #--------------------------------------------------------------------------
    
    vo['Ws']=np.maximum(0,vo['Ws'])
    vo['Wsp']=np.maximum(0,vo['Wsp'])
    
    #--------------------------------------------------------------------------    
    # Include rainfall fraction
    #--------------------------------------------------------------------------
    
    if par['Include Rainfall Fraction']=='Yes':
        vo['RF']=np.minimum(1,np.maximum(0,Pr/Ps))
        
    return vo

'''============================================================================

HYDROMETEOROLOGY CONSTANTS

============================================================================'''

def HydroMetCon():
    
    con={}
    
    # Air density (kg m-3)
    con['RhoAir']=1.2
    
    # Specific heat capacity (J kg-1 K-1)
    con['CpAir']=1010
    
    # Latent heat of vaporization (J kg-1)
    con['Lam']=2460000
    
    # Preistly taylor coefficient
    con['Alpha_PT']=1.26
    
    # Daylength (s)
    con['DayLength']=86400
    
    # Conversion of downwelling short wave solar radiation to net radiation
    con['Rswd2Rn_Slope']=11.96
    con['Rswd2Rn_AddOffset']=3.46
    
    # Convert net shortwave radiation (W m-2) to net radiation (W m-2)
    # Parameters from this were fitted to data at DF49 and agree roughly with 
    # Landsberg's textbook.
    con['Rswn2Rn_Slope']=0.837
    con['Rswn2Rn_AddOffset']=-23.58
    
    # Albedo
    Albedo={}
    Albedo['Forest Coniferous']=0.04
    Albedo['Forest Deciduous']=0.09
    con['Albedo']=Albedo
    
    # Days in month
    con['DIM']=np.array([31,28,31,30,31,30,31,31,30,31,30,31])
    
    return con

'''============================================================================

CALCULATE PSYCHROMETRIC TERM

============================================================================'''

def GetPsychrometric(ta,Units):
    
    # This method compares closely with Fernandes et al. (2007)
    
    if Units=='Pressure':
        # Units: Pa K=1
        b=np.array([-6.02240896358241e-005,0.0616092436974788,64.9608123249299])
    elif Units=='Density':
        # Units: kg m-3 K-1
        b=np.array([4.2507002801123e-009,-1.40523109243698e-006,0.000515354446778711])

    y=b[0]*ta**2 + b[1]*ta+b[2]
    
    return y

'''============================================================================

CALCULATE SATURATION VAPOUR PRESSURE VS. TEMPERATURE SLOPE

============================================================================'''

def GetSVPSlope(ta,Units):
    
    # Calculate slope of the saturation vapour pressure - temperature curve 
    # using the method METH based on input of air temperature (deg C).
    
    if Units=='Pressure':
        # Units: Pa K=1
        b=np.array([0.000011482039374,0.001256498041862,0.078296395471144,2.846599268013546,44.494094675319538])
    elif Units=='Density':
        # Units: kg m-3 K-1
        b=np.array([4.5645788679845e-011,7.49675848747055e-009,5.23844963086449e-007,2.00663120848879e-005,0.000335075613241248])
    
    y=b[0]*ta**4 + b[1]*ta**3 + b[2]*ta**2 + b[3]*ta + b[4]
    
    return y