'''
This file contains classes and functions for representing, solving, and simulating
agents who must allocate their resources among consumption, saving in a risk-free
asset (with a low return), and saving in a risky asset (with higher average return).
'''
import numpy as np
import matplotlib.pyplot as plt
from scipy.optimize import minimize_scalar
from copy import copy, deepcopy
from HARK import Solution, NullFunc, AgentType # Basic HARK features
from HARK.ConsumptionSaving.ConsIndShockModel import(
    PerfForesightConsumerType,  # For .__init__
    IndShockConsumerType,       # PortfolioConsumerType inherits from it
    ValueFunc,                  # For representing 1D value function
    MargValueFunc,              # For representing 1D marginal value function
    utility_inv,                # Inverse CRRA utility function
)
from HARK.ConsumptionSaving.ConsGenIncProcessModel import(
    ValueFunc2D,                # For representing 2D value function
    MargValueFunc2D             # For representing 2D marginal value function
)
from HARK.utilities import (
    approxLognormal,            # For approximating the lognormal return factor
    combineIndepDstns,          # For combining the income distribution with the risky return distribution
)
from HARK.simulation import drawLognormal # Random draws for simulating agents
from HARK.interpolation import(
        LinearInterp,           # Piecewise linear interpolation
        LinearInterpOnInterp1D, # 2D interpolator
        ConstantFunction,       # Interpolator-like class that returns constant value
        IdentityFunction        # Interpolator-like class that returns one of its arguments
)
import HARK.ConsumptionSaving.ConsumerParameters as Params


# Define a class to represent the single period solution of the portfolio choice problem
class PortfolioSolution(Solution):
    '''
    A class for representing the single period solution of the portfolio choice model.
    
    Parameters
    ----------
    cFuncAdj : Interp1D
        Consumption function over normalized market resources when the agent is able
        to adjust their portfolio shares.
    ShareFuncAdj : Interp1D
        Risky share function over normalized market resources when the agent is able
        to adjust their portfolio shares.
    vFuncAdj : ValueFunc
        Value function over normalized market resources when the agent is able to
        adjust their portfolio shares.
    vPfuncAdj : MargValueFunc
        Marginal value function over normalized market resources when the agent is able
        to adjust their portfolio shares.
    cFuncFxd : Interp2D
        Consumption function over normalized market resources and risky portfolio share
        when the agent is NOT able to adjust their portfolio shares, so they are fixed.
    ShareFuncFxd : Interp2D
        Risky share function over normalized market resources and risky portfolio share
        when the agent is NOT able to adjust their portfolio shares, so they are fixed.
        This should always be an IdentityFunc, by definition.
    vFuncFxd : ValueFunc2D
        Value function over normalized market resources and risky portfolio share when
        the agent is NOT able to adjust their portfolio shares, so they are fixed.
    dvdmFuncFxd : MargValueFunc2D
        Marginal value of mNrm function over normalized market resources and risky
        portfolio share when the agent is NOT able to adjust their portfolio shares,
        so they are fixed.
    dvdsFuncFxd : MargValueFunc2D
        Marginal value of Share function over normalized market resources and risky
        portfolio share when the agent is NOT able to adjust their portfolio shares,
        so they are fixed.
    mNrmMin
    '''
    distance_criteria = ['vPfuncAdj']

    def __init__(self,
        cFuncAdj=None,
        ShareFuncAdj=None,
        vFuncAdj=None,
        vPfuncAdj=None,
        cFuncFxd=None,
        ShareFuncFxd=None,
        vFuncFxd=None,
        dvdmFuncFxd=None,
        dvdsFuncFxd=None
    ):

        # Change any missing function inputs to NullFunc
        if cFuncAdj is None:
            cFuncAdj = NullFunc()
        if cFuncFxd is None:
            cFuncFxd = NullFunc()
        if ShareFuncAdj is None:
            ShareFuncAdj = NullFunc()
        if ShareFuncFxd is None:
            ShareFuncFxd = NullFunc()
        if vFuncAdj is None:
            vFuncAdj = NullFunc()
        if vFuncFxd is None:
            vFuncFxd = NullFunc()
        if vPfuncAdj is None:
            vPfuncAdj = NullFunc()
        if dvdmFuncFxd is None:
            dvdmFuncFxd = NullFunc()
        if dvdsFuncFxd is None:
            dvdsFuncFxd = NullFunc()
            
        # Set attributes of self
        self.cFuncAdj = cFuncAdj
        self.cFuncFxd = cFuncFxd
        self.ShareFuncAdj = ShareFuncAdj
        self.ShareFuncFxd = ShareFuncFxd
        self.vFuncAdj = vFuncAdj
        self.vFuncFxd = vFuncFxd
        self.vPfuncAdj = vPfuncAdj
        self.dvdmFuncFxd = dvdmFuncFxd
        self.dvdsFuncFxd = dvdsFuncFxd
        
        
class PortfolioConsumerType(IndShockConsumerType):
    """
    A consumer type with a portfolio choice. This agent type has log-normal return
    factors. Their problem is defined by a coefficient of relative risk aversion,
    intertemporal discount factor, risk-free interest factor, and time sequences of
    permanent income growth rate, survival probability, and permanent and transitory
    income shock standard deviations (in logs).  The agent may also invest in a risky
    asset, which has a higher average return than the risk-free asset.  He *might*
    have age-varying beliefs about the risky-return; if he does, then "true" values
    of the risky asset's return distribution must also be specified.
    """
    poststate_vars_ = ['aNrmNow', 'pLvlNow', 'ShareNow', 'FxdNow']
    time_inv_ = deepcopy(IndShockConsumerType.time_inv_)
    time_inv_ = time_inv_ + ['AdjustPrb']

    def __init__(self, cycles=1, time_flow=True, verbose=False, quiet=False, **kwds):
        params = Params.init_portfolio.copy()
        params.update(kwds)
        kwds = params

        # Initialize a basic consumer type
        PerfForesightConsumerType.__init__(
            self,
            cycles=cycles,
            time_flow=time_flow,
            verbose=verbose,
            quiet=quiet,
            **kwds
        )
        
        # Set the solver for the portfolio model, and update various constructed attributes
        self.solveOnePeriod = solveConsPortfolio
        self.update()
        
        
    def preSolve(self):
        AgentType.preSolve(self)
        self.updateSolutionTerminal()


    def update(self):
        IndShockConsumerType.update(self)
        self.updateRiskyDstn()
        self.updateShockDstn()
        self.updateShareGrid()
        self.updateShareLimit()
        
        
    def updateSolutionTerminal(self):
        '''
        Solves the terminal period of the portfolio choice problem.  The solution is
        trivial, as usual: consume all market resources, and put nothing in the risky
        asset (because you have nothing anyway).
        
        Parameters
        ----------
        None

        Returns
        -------
        None
        '''
        # Consume all market resources: c_T = m_T
        cFuncAdj_terminal = IdentityFunction()
        cFuncFxd_terminal = IdentityFunction(i_dim=0, n_dims=2)
        
        # Risky share is irrelevant-- no end-of-period assets; set to zero
        ShareFuncAdj_terminal = ConstantFunction(0.)
        ShareFuncFxd_terminal = IdentityFunction(i_dim=1, n_dims=2)
        
        # Value function is simply utility from consuming market resources
        vFuncAdj_terminal = ValueFunc(cFuncAdj_terminal, self.CRRA)
        vFuncFxd_terminal = ValueFunc2D(cFuncFxd_terminal, self.CRRA)
        
        # Marginal value of market resources is marg utility at the consumption function
        vPfuncAdj_terminal = MargValueFunc(cFuncAdj_terminal, self.CRRA)
        dvdmFuncFxd_terminal = MargValueFunc2D(cFuncFxd_terminal, self.CRRA)
        dvdsFuncFxd_terminal = ConstantFunction(0.) # No future, no marg value of Share
        
        # Construct the terminal period solution
        self.solution_terminal = PortfolioSolution(
                cFuncAdj=cFuncAdj_terminal,
                ShareFuncAdj=ShareFuncAdj_terminal,
                vFuncAdj=vFuncAdj_terminal,
                vPfuncAdj=vPfuncAdj_terminal,
                cFuncFxd=cFuncFxd_terminal,
                ShareFuncFxd=ShareFuncFxd_terminal,
                vFuncFxd=vFuncFxd_terminal,
                dvdmFuncFxd=dvdmFuncFxd_terminal,
                dvdsFuncFxd=dvdsFuncFxd_terminal
        )
        
        
    def updateRiskyDstn(self):
        '''
        Creates the attributes RiskyDstn from the primitive attributes RiskyAvg,
        RiskyStd, and RiskyCount, approximating the (perceived) distribution of
        returns in each period of the cycle.
        
        Parameters
        ----------
        None

        Returns
        -------
        None
        '''
        # Determine whether this instance has time-varying risk perceptions
        if (type(self.RiskyAvg) is list) and (type(self.RiskyStd) is list) and (len(self.RiskyAvg) == len(self.RiskyStd)) and (len(self.RiskyAvg) == self.T_cycle):
            self.addToTimeVary('RiskyAvg','RiskyStd')
        elif (type(self.RiskyStd) is list) or (type(self.RiskyAvg) is list):
            raise AttributeError('If RiskyAvg is time-varying, then RiskyStd must be as well, and they must both have length of T_cycle!')
        else:
            self.addToTimeInv('RiskyAvg','RiskyStd')
        
        # Generate a discrete approximation to the risky return distribution if the
        # agent has age-varying beliefs about the risky asset
        if 'RiskyAvg' in self.time_vary:
            RiskyDstn = []
            time_orig = self.time_flow
            self.timeFwd()
            for t in range(self.T_cycle):
                RiskyAvgSqrd = self.RiskyAvg[t] ** 2
                RiskyVar = self.RiskyStd[t] ** 2
                mu = np.log(self.RiskyAvg[t] / (np.sqrt(1. + RiskyVar / RiskyAvgSqrd)))
                sigma = np.sqrt(np.log(1. + RiskyVar / RiskyAvgSqrd))
                RiskyDstn.append(approxLognormal(self.RiskyCount, mu=mu, sigma=sigma))
            self.RiskyDstn = RiskyDstn
            self.addToTimeVary('RiskyDstn')
            if not time_orig:
                self.timeRev()
                
        # Generate a discrete approximation to the risky return distribution if the
        # agent does *not* have age-varying beliefs about the risky asset (base case)
        else:
            RiskyAvgSqrd = self.RiskyAvg ** 2
            RiskyVar = self.RiskyStd ** 2
            mu = np.log(self.RiskyAvg / (np.sqrt(1. + RiskyVar / RiskyAvgSqrd)))
            sigma = np.sqrt(np.log(1. + RiskyVar / RiskyAvgSqrd))
            self.RiskyDstn = approxLognormal(self.RiskyCount, mu=mu, sigma=sigma)
            self.addToTimeInv('RiskyDstn')
            
            
    def updateShockDstn(self):
        '''
        Combine the income shock distribution (over PermShk and TranShk) with the
        risky return distribution (RiskyDstn) to make a new attribute called ShockDstn.
        
        Parameters
        ----------
        None

        Returns
        -------
        None
        '''
        if 'RiskyDstn' in self.time_vary:
            self.ShockDstn = [combineIndepDstns(self.IncomeDstn[t], self.RiskyDstn[t]) for t in range(self.T_cycle)]
        else:
            self.ShockDstn = [combineIndepDstns(self.IncomeDstn[t], self.RiskyDstn) for t in range(self.T_cycle)]
        self.addToTimeVary('ShockDstn')
        
        
    def updateShareGrid(self):
        '''
        Creates the attribute ShareGrid as an evenly spaced grid on [0.,1.], using
        the primitive parameter ShareCount.
        
        Parameters
        ----------
        None

        Returns
        -------
        None
        '''
        self.ShareGrid = np.linspace(0.,1.,self.ShareCount)
        self.addToTimeInv('ShareGrid')
        
        
    def updateShareLimit(self):
        '''
        Creates the attribute ShareLimit, representing the limiting lower bound of
        risky portfolio share as mNrm goes to infinity.
        
        Parameters
        ----------
        None

        Returns
        -------
        None
        '''
        if 'RiskyDstn' in self.time_vary:
            time_orig = self.time_flow
            self.timeFwd()
            self.ShareLimit = []
            for t in range(self.T_cycle):
                RiskyDstn = self.RiskyDstn[t]
                temp_f = lambda s : -((1.-self.CRRA)**-1)*np.dot((self.Rfree + s*(RiskyDstn[1]-self.Rfree))**(1.-self.CRRA), RiskyDstn[0])
                SharePF = minimize_scalar(temp_f, bounds=(0.0, 1.0), method='bounded').x
                self.ShareLimit.append(SharePF)
            self.addToTimeVary('ShareLimit')
            if not time_orig:
                self.timeRev()
        
        else:
            RiskyDstn = self.RiskyDstn
            temp_f = lambda s : -((1.-self.CRRA)**-1)*np.dot((self.Rfree + s*(RiskyDstn[1]-self.Rfree))**(1.-self.CRRA), RiskyDstn[0])
            SharePF = minimize_scalar(temp_f, bounds=(0.0, 1.0), method='bounded').x
            self.ShareLimit = SharePF
            self.addToTimeInv('ShareLimit')
        
                
# Define a non-object-oriented one period solver
def solveConsPortfolio(solution_next,ShockDstn,LivPrb,DiscFac,CRRA,Rfree,PermGroFac,
                       BoroCnstArt,aXtraGrid,ShareGrid,vFuncBool,AdjustPrb,ShareLimit):
    '''
    Solve the one period problem for a portfolio-choice consumer.
    
    Parameters
    ----------
    solution_next : PortfolioSolution
        Solution to next period's problem.
    ShockDstn : [np.array]
        List with four arrays: discrete probabilities, permanent income shocks,
        transitory income shocks, and risky returns.
    LivPrb : float
        Survival probability; likelihood of being alive at the beginning of
        the succeeding period.
    DiscFac : float
        Intertemporal discount factor for future utility.
    CRRA : float
        Coefficient of relative risk aversion.
    Rfree : float
        Risk free interest factor on end-of-period assets.
    PermGroFac : float
        Expected permanent income growth factor at the end of this period.
    BoroCnstArt: float or None
        Borrowing constraint for the minimum allowable assets to end the
        period with.  In this model, it is *required* to be zero.
    aXtraGrid: np.array
        Array of "extra" end-of-period asset values-- assets above the
        absolute minimum acceptable level.
    ShareGrid : np.array
        Array of risky portfolio shares on which to define the interpolation
        of the consumption function when Share is fixed.
    vFuncBool: boolean
        An indicator for whether the value function should be computed and
        included in the reported solution.
    AdjustPrb : float
        Probability that the agent will be able to update his portfolio share.
    ShareLimit : float
        Limiting lower bound of risky portfolio share as mNrm approaches infinity.

    Returns
    -------
    solution_now : PortfolioSolution
        The solution to the single period consumption-saving with portfolio choice
        problem.  Includes two consumption and risky share functions: one for when
        the agent can adjust his portfolio share (Adj) and when he can't (Fxd).
    '''
    # Make sure the individual is liquidity constrained.  Allowing a consumer to
    # borrow *and* invest in an asset with unbounded (negative) returns is a bad mix.
    if BoroCnstArt != 0.0:
        raise AttributeError('PortfolioConsumerType must have BoroCnstArt=0.0!')
        
    # Unpack next period's solution
    vPfuncAdj_next = solution_next.vPfuncAdj
    dvdmFuncFxd_next = solution_next.dvdmFuncFxd
    dvdsFuncFxd_next = solution_next.dvdsFuncFxd
    vFuncAdj_next = solution_next.vFuncAdj
    vFuncFxd_next = solution_next.vFuncFxd
    
    # Unpack the shock distribution
    ShockPrbs_next = ShockDstn[0]
    PermShks_next  = ShockDstn[1]
    TranShks_next  = ShockDstn[2]
    Risky_next     = ShockDstn[3]
    
    # Make tiled arrays to calculate future realizations of mNrm and Share; dimension order: mNrm, Share, shock
    aNrmGrid = np.insert(aXtraGrid, 0, 0.0) # Add an asset point at exactly zero
    aNrm_N = aNrmGrid.size
    Share_N = ShareGrid.size
    Shock_N = ShockPrbs_next.size
    aNrm_tiled = np.tile(np.reshape(aNrmGrid, (aNrm_N,1,1)), (1,Share_N,Shock_N))
    Share_tiled = np.tile(np.reshape(ShareGrid, (1,Share_N,1)), (aNrm_N,1,Shock_N))
    ShockPrbs_tiled = np.tile(np.reshape(ShockPrbs_next, (1,1,Shock_N)), (aNrm_N,Share_N,1))
    PermShks_tiled = np.tile(np.reshape(PermShks_next, (1,1,Shock_N)), (aNrm_N,Share_N,1))
    TranShks_tiled = np.tile(np.reshape(TranShks_next, (1,1,Shock_N)), (aNrm_N,Share_N,1))
    Risky_tiled = np.tile(np.reshape(Risky_next, (1,1,Shock_N)), (aNrm_N,Share_N,1))
    
    # Calculate future realizations of market resources
    Rport = (1.-Share_tiled)*Rfree + Share_tiled*Risky_tiled
    mNrm_next = Rport*aNrm_tiled/(PermShks_tiled*PermGroFac) + TranShks_tiled
    Share_next = Share_tiled
    
    # Evaluate realizations of marginal value of market resources next period
    dvdmAdj_next = vPfuncAdj_next(mNrm_next)
    if AdjustPrb < 1.:
        dvdmFxd_next = dvdmFuncFxd_next(mNrm_next, Share_next)
        dvdm_next = AdjustPrb*dvdmAdj_next + (1.-AdjustPrb)*dvdmFxd_next # Combine by adjustment probability
    else: # Don't bother evaluating if there's no chance that portfolio share is fixed
        dvdm_next = dvdmAdj_next

    # Evaluate realizations of marginal value of risky share next period
    dvdsAdj_next = np.zeros_like(mNrm_next) # No marginal value of Share if it's a free choice!
    if AdjustPrb < 1.:
        dvdsFxd_next = dvdsFuncFxd_next(mNrm_next, Share_next)
        dvds_next = AdjustPrb*dvdsAdj_next + (1.-AdjustPrb)*dvdsFxd_next # Combine by adjustment probability
    else: # Don't bother evaluating if there's no chance that portfolio share is fixed
        dvds_next = dvdsAdj_next
    
    # If the value function has been requested, evaluate realizations of value
    if vFuncBool:
        vAdj_next = vFuncAdj_next(mNrm_next)
        if AdjustPrb < 1.:
            vFxd_next = vFuncFxd_next(mNrm_next, Share_next)
            v_next = AdjustPrb*vAdj_next + (1.-AdjustPrb)*vFxd_next
        else: # Don't bother evaluating if there's no chance that portfolio share is fixed
            v_next = vAdj_next
    else:
        v_next = np.zeros_like(dvdm_next) # Trivial array
        
    # Calculate end-of-period marginal value of assets by taking expectations
    temp_fac_A = (PermShks_tiled*PermGroFac)**(-CRRA) # Will use this in a couple places
    EndOfPrddvda = DiscFac*np.sum(ShockPrbs_tiled*Rport*temp_fac_A*dvdm_next, axis=2)
    EndOfPrddvdaNvrs = EndOfPrddvda**(-1./CRRA)
    
    # Calculate end-of-period value by taking expectations
    temp_fac_B = (PermShks_tiled*PermGroFac)**(1.-CRRA) # Will use this below
    EndOfPrdv    = DiscFac*np.sum(ShockPrbs_tiled*temp_fac_B*v_next, axis=2)
    
    # Calculate end-of-period marginal value of risky portfolio share by taking expectations
    Rxs = Rport - Rfree
    EndOfPrddvds = DiscFac*np.sum(ShockPrbs_tiled*(Rxs*aNrm_tiled*temp_fac_A*dvdm_next + temp_fac_B*dvds_next), axis=2)
    
    # For values of aNrm at which the agent wants to put more than 100% into risky asset, constrain them
    FOC_s = EndOfPrddvds
    Share_now   = np.zeros_like(aNrmGrid) # Initialize to putting everything in safe asset
    cNrmAdj_now = np.zeros_like(aNrmGrid)
    constrained = FOC_s[:,-1] > 0. # If agent wants to put more than 100% into risky asset, he is constrained
    Share_now[constrained] = 1.0
    Share_now[0] = 1. # aNrm=0, so there's no way to "optimize" the portfolio
    cNrmAdj_now[constrained] = EndOfPrddvda[constrained,-1]**(-1./CRRA) # Get consumption when share-constrained
    cNrmAdj_now[0] = EndOfPrddvda[0,-1]**(-1./CRRA) # Consumption when aNrm==0 does not depend on Share
    
    # For each value of aNrm, find the value of Share such that FOC-Share == 0
    crossing = np.logical_and(FOC_s[:,1:] <= 0., FOC_s[:,:-1] >= 0.)
    for j in range(aNrm_N):
        if Share_now[j] == 0.:
            try:
                idx = np.argwhere(crossing[j,:])[-1][0]
                bot_s = ShareGrid[idx]
                top_s = ShareGrid[idx+1]
                bot_f = FOC_s[j,idx]
                top_f = FOC_s[j,idx+1]
                bot_c = EndOfPrddvdaNvrs[j,idx]
                top_c = EndOfPrddvdaNvrs[j,idx+1]
                alpha = 1. - top_f/(top_f-bot_f)
                Share_now[j] = (1.-alpha)*bot_s + alpha*top_s
                cNrmAdj_now[j] = (1.-alpha)*bot_c + alpha*top_c
            except:
                print('No optimal controls found for a=' + str(aNrmGrid[j]))
                
    # Calculate the endogenous mNrm gridpoints when the agent adjusts his portfolio,
    # and add an additional point at (mNrm,cNrm)=(0,0)
    mNrmAdj_now = np.insert(aNrmGrid + cNrmAdj_now, 0, 0.0)
    cNrmAdj_now = np.insert(cNrmAdj_now, 0, 0.0)
    Share_now   = np.insert(Share_now, 0, 1.0)
    
    # Construct the consumption and risky share functions when the agent can adjust
    cFuncAdj_now = LinearInterp(mNrmAdj_now, cNrmAdj_now)
    ShareFuncAdj_now = LinearInterp(mNrmAdj_now, Share_now, intercept_limit=ShareLimit, slope_limit=0.0)
    
    # Construct the marginal value (of mNrm) function when the agent can adjust
    vPfuncAdj_now = MargValueFunc(cFuncAdj_now, CRRA)
    
    # Construct the consumption function when the agent *can't* adjust the risky share, as well
    # as the marginal value of Share function
    cFuncFxd_by_Share = []
    dvdsFuncFxd_by_Share = []
    for j in range(Share_N):
        cNrmFxd_temp = EndOfPrddvdaNvrs[:,j]
        mNrmFxd_temp = aNrmGrid + cNrmFxd_temp
        cFuncFxd_by_Share.append(LinearInterp(np.insert(mNrmFxd_temp, 0, 0.0), np.insert(cNrmFxd_temp, 0, 0.0)))
        dvdsFuncFxd_by_Share.append(LinearInterp(np.insert(mNrmFxd_temp, 0, 0.0), np.insert(EndOfPrddvds[:,j], 0, EndOfPrddvds[0,j])))
    cFuncFxd_now = LinearInterpOnInterp1D(cFuncFxd_by_Share, ShareGrid)
    dvdsFuncFxd_now = LinearInterpOnInterp1D(dvdsFuncFxd_by_Share, ShareGrid)
    
    # The share function when the agent can't adjust is trivial
    ShareFuncFxd_now = IdentityFunction(i_dim=1, n_dims=2)
    
    # Construct the marginal value of mNrm function when the agent can't adjust his share
    dvdmFuncFxd_now = MargValueFunc2D(cFuncFxd_now, CRRA)    

    # Create and return this period's solution
    return PortfolioSolution(
            cFuncAdj = cFuncAdj_now,
            ShareFuncAdj = ShareFuncAdj_now,
            vPfuncAdj = vPfuncAdj_now,
            cFuncFxd = cFuncFxd_now,
            ShareFuncFxd = ShareFuncFxd_now,
            dvdmFuncFxd = dvdmFuncFxd_now,
            dvdsFuncFxd = dvdsFuncFxd_now
    )
    
        
if __name__ == '__main__':
    from time import time
    
    TestType = PortfolioConsumerType()
    TestType.cycles = 0
    t0 = time()
    TestType.solve(True)
    t1 = time()
    print('Solving an infinite horizon portfolio choice problem took ' + str(t1-t0) + ' seconds.')
    
    M = np.linspace(0.,1.,200)
    for s in np.linspace(0.,1.,21):
        f = lambda m : TestType.solution[0].dvdsFuncFxd(m, s*np.ones_like(m))
        plt.plot(M, f(M))
    plt.show()
        
        