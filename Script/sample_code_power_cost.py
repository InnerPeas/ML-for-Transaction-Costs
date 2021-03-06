import os
import numpy as np
import matplotlib.pyplot as plt
import torch
import math
from tqdm import tqdm
torch.manual_seed(1)
# check if CUDA is available
train_on_gpu = torch.cuda.is_available()
if not train_on_gpu:
    print('CUDA is not available.  Training on CPU ...')
else:
    print('CUDA is available!  Training on GPU ...')
import pandas as pd
from joblib import Parallel, delayed
from sample_code_Deep_Hedging import *
from sample_code_FBSDE import *

os.environ['KMP_DUPLICATE_LIB_OK']='True'

# Market Variables
q=3/2
S_OUTSTANDING = 245714618646 # Total share outstanding
TIME          = 21 # Trading Horizon
TIME_STEP     = 168 # Discretization step
DT            = TIME/TIME_STEP 
GAMMA_BAR     = 8.30864e-14 # Aggregate risk aversion
KAPPA         = 1.
GAMMA_1       = GAMMA_BAR*(KAPPA+1)/KAPPA # Absolute risk aversion for both agents
GAMMA_2       = GAMMA_BAR*(KAPPA+1)
GAMMA_HAT     = (GAMMA_1-GAMMA_2)/(GAMMA_1+GAMMA_2)
GAMMA         = 0.5*(GAMMA_1+GAMMA_2)
XI_1          = 2.33e10 # Endowment volatility
XI_2          = -XI_1
XI            = XI_1 
PHI_INITIAL   = S_OUTSTANDING*KAPPA/(KAPPA+1) # Initial allocation
ALPHA         = 1.8788381 # Frictionless volatility
ALPHA2        = ALPHA**2
MU_BAR        =  0.5*GAMMA*S_OUTSTANDING*ALPHA**2 # Frictionless return
LAM           = 5.22e-6 # Transaction penalty

path_Q=os.path.abspath(os.path.join(os.path.dirname(__file__),".."))+"/Data/trading{}_cost{}/DeepQ/".format(TIME,q)
isExist = os.path.exists(path_Q)
if not isExist:  
  os.makedirs(path_Q)
path_FBSDE=os.path.abspath(os.path.join(os.path.dirname(__file__),".."))+"/Data/trading{}_cost{}/FBSDE/".format(TIME,q)
isExist = os.path.exists(path_FBSDE)
if not isExist:  
  os.makedirs(path_FBSDE)
path=os.path.abspath(os.path.join(os.path.dirname(__file__),".."))+"/Data/trading{}_cost{}/".format(TIME,q) 

# FBSDE solver
# Batch size
N_SAMPLES = 300
# Dimension of Brownian Motion
BM_DIM    = 1
# Dimension of Forward Variables
FOR_DIM   = 1 
# Number of Backward Variables
BACK_DIM  = 2
# Training hyperparameter 
LR         = 1e-3
EPOCHS     = 10

test = FBSDESolver(
                 path_FBSDE=path_FBSDE,
                 dt     = DT,
                 T         =TIME,
                 time_step = TIME_STEP,
                 n_samples = N_SAMPLES,
                 for_dim   = FOR_DIM,
                 back_dim  = BACK_DIM,
                 bm_dim    = BM_DIM,
                 s         = S_OUTSTANDING,
                 mu_bar    = MU_BAR,
                 alpha     = ALPHA,
                 gamma_bar = GAMMA_BAR,
                 gamma     = GAMMA,
                 gamma_hat = GAMMA_HAT,
                 xi        = XI,
                 lam       = LAM,
                 lam_dyn   = False,
                 q=q)
test.train(DECAY_LR=1.,epochs=EPOCHS ,path_size=N_SAMPLES,bm_dim= BM_DIM,time_step= TIME_STEP,dt = DT)
#test.restore_system()

# Deep Q-learning
EPOCH_Q=5
LR_Q = 1e-2 
N_SAMPLE_Q=300 
TIME_STEP_Q=TIME_STEP
result_Q=TRAIN_Utility(train_on_gpu,path_Q,XI,PHI_INITIAL,q,S_OUTSTANDING,GAMMA,LAM,MU_BAR,ALPHA,TIME,EPOCH=EPOCH_Q,n_samples=N_SAMPLE_Q,time_step=TIME_STEP_Q,HIDDEN_DIM_Utility=[10,15,10],loading=False,
      LR_Utility=LR_Q,OPT_Utility="ADAM",
      saving=[10]) 
model_list_Q=result_Q['model_list']
loss_arr_Q=result_Q['loss']

# TEST
# the leading order ODE is solved by Mathematica and the solution TABLE is stored in EVA.txt
pathEVA='../Data/'
EVA=np.loadtxt(pathEVA+"EVA.txt") #value of g(x) of TABLE
EVA=np.vstack((np.linspace(0,50,EVA.shape[0]), EVA)).T
XM=50
EVA_STEP=EVA[1,0]-EVA[0,0]
def new_g_q_Mathematica(x):
    if np.abs(x)>XM:
        g_value= -np.sign(x)*q*(q-1)**(-(q-1)/q)*np.abs(x)**(2*(q-1)/q)           
    else:
        g_value=-np.sign(x)*np.abs(EVA[int(round(np.abs(x)/EVA_STEP)),1])
    return g_value
new_g_q_Mathematica_vec=np.vectorize(new_g_q_Mathematica)

def TEST(dW,model_list_Utility,test_samples):
    with torch.no_grad():
            T=len(model_list_Utility)
            for model in model_list_Utility:
                model.eval()
            PHI_0_on_s = torch.ones(test_samples)*PHI_INITIAL/S_OUTSTANDING
            PHI_0 = torch.ones(test_samples)*PHI_INITIAL
            DUMMY_1 = torch.ones(test_samples).reshape((test_samples, 1))
            if train_on_gpu:
                PHI_0_on_s = PHI_0_on_s.to(device="cuda")
                PHI_0 = PHI_0.to(device="cuda")
                DUMMY_1 = DUMMY_1.to(device="cuda")
            ### XI: (N,T+1)
            W=torch.cumsum(dW[:,0,:], dim=1)
            W=torch.cat((torch.zeros((test_samples,1)),W),dim=1) 
            XI_W_on_s = XI* W /S_OUTSTANDING
            if train_on_gpu:
                XI_W_on_s = XI_W_on_s.to(device="cuda")
            ### UTILITY
            PHI_on_s = torch.zeros((test_samples, T + 1))
            if train_on_gpu:
                PHI_on_s = PHI_on_s.to(device="cuda")
            PHI_on_s[:,0] = PHI_on_s[:,0]+PHI_0_on_s.reshape((-1,))
            PHI_dot_on_s = torch.zeros((test_samples, T ))
            if train_on_gpu:
                PHI_dot_on_s = PHI_dot_on_s.to(device="cuda")
            for t in range(T):
                ### UTILITY
                if train_on_gpu:
                    t_tensor=t/T*TIME*torch.ones(test_samples).reshape(-1,1).cuda()            
                    x_Utility=torch.cat((PHI_on_s[:,t].reshape(-1,1),XI_W_on_s[:,t].reshape(-1,1),t_tensor),dim=1).cuda()
                else: 
                    t_tensor=t/T*TIME*torch.ones(test_samples).reshape(-1,1)
                    x_Utility=torch.cat((PHI_on_s[:,t].reshape(-1,1),XI_W_on_s[:,t].reshape(-1,1),t_tensor),dim=1)        
                PHI_dot_on_s[:,t] = model_list_Utility[t](x_Utility).reshape(-1,)
                PHI_on_s[:,(t+1)] = PHI_on_s[:,t].reshape(-1)+PHI_dot_on_s[:,(t)].reshape(-1)*TIME/T
            for model in model_list_Utility:
                model.train()  
            # Leading Order by Mathematica
            PHI_dot_APP_Mathematica = np.zeros((test_samples,T))
            PHI_APP_Mathematica = np.zeros((test_samples,T+1))            
            PHI_APP_Mathematica[:,0] = PHI_APP_Mathematica[:,0]+PHI_0.cpu().numpy().reshape((-1,))
            for t in range(T):
                XIt=XI_W_on_s.cpu().numpy()[:,t] *S_OUTSTANDING
                PHIt=PHI_APP_Mathematica[:,t]
                PHIBARt=MU_BAR/GAMMA/ALPHA/ALPHA-XIt/ALPHA
                xxx=2**((q-1)/(q+2))*(q*GAMMA*ALPHA*ALPHA/LAM)**(1/(q+2))*(ALPHA/XI)**(2*q/(q+2))*(PHIt-PHIBARt)
                PHI_dot_APP_Mathematica[:,t]=(-np.sign(PHIt-PHIBARt)
                          *(q*GAMMA*XI**4/8/LAM/ALPHA/ALPHA)**(1/(q+2))
                          *abs(new_g_q_Mathematica_vec(xxx)/q)**(1/(q-1))
                          )
                PHI_APP_Mathematica[:,t+1]=PHI_APP_Mathematica[:,t]+PHI_dot_APP_Mathematica[:,t]*TIME/T
    result={
        "T":T,
        "Sample_XI_on_s":XI_W_on_s,
        "PHI_dot_on_s_Utility":PHI_dot_on_s,
        "PHI_dot_APP_Mathematica":PHI_dot_APP_Mathematica,
        "PHI_dot_on_s_APP_Mathematica":PHI_dot_APP_Mathematica/S_OUTSTANDING,
        "PHI_on_s_Utility":PHI_on_s,
        "PHI_APP_Mathematica":PHI_APP_Mathematica,
        "PHI_on_s_APP_Mathematica":PHI_APP_Mathematica/S_OUTSTANDING,
        }
    return(result)

def big_test(test_samples,REPEAT,model_list_Utility=model_list_Q):
    TARGET_test = torch.zeros(test_samples).reshape((test_samples, 1))
    mu_Utility = 0.0
    mu2_Utility = 0.0
    FBSDELOSS_Utility = 0.0
    mu_FBSDE = 0.0
    mu2_FBSDE = 0.0
    FBSDELOSS_FBSDE = 0.0
    mu_LO = 0.0
    mu2_LO = 0.0
    FBSDELOSS_LO = 0.0
    for itr in tqdm(range(REPEAT)):
        dW_test = train_data(n_samples=test_samples,bm_dim= BM_DIM,time_step= TIME_STEP,dt = DT)
        dW_test_FBSDE=dW_test
        W_test_FBSDE=torch.cumsum(dW_test_FBSDE[:,0,:], dim=1) #ttttt
        W_test_FBSDE=torch.cat((torch.zeros((test_samples,1)),W_test_FBSDE),dim=1) 
        XI_test_on_s_FBSDE = XI* W_test_FBSDE /S_OUTSTANDING
        if train_on_gpu:
          XI_test_on_s_FBSDE = XI_test_on_s_FBSDE.to(device="cuda")
        Test_result=TEST(dW_test,model_list_Utility,test_samples)
        T=Test_result["T"]
        XI_test_on_s=Test_result["Sample_XI_on_s"]
        PHI_dot_on_s_Utility=Test_result["PHI_dot_on_s_Utility"]
        PHI_dot_APP_on_s=Test_result["PHI_dot_on_s_APP_Mathematica"]
        PHI_on_s_Utility=Test_result["PHI_on_s_Utility"]
        PHI_APP_on_s=Test_result["PHI_on_s_APP_Mathematica"]
        test.system.sample_phi(dW_test_FBSDE)
        PHI_dot_FBSDE=(test.system.D_Delta_t_value*XI-XI/ALPHA*dW_test_FBSDE[:,0,:].cpu().numpy())/DT
        PHI_dot_FBSDE_on_s=PHI_dot_FBSDE/S_OUTSTANDING
        PHI_FBSDE=test.system.Delta_t_value*XI+MU_BAR/GAMMA/ALPHA/ALPHA-(S_OUTSTANDING*XI_test_on_s_FBSDE/ALPHA).cpu().numpy()
        PHI_FBSDE_on_s=PHI_FBSDE/S_OUTSTANDING    
        ### UTILITY
        FBSDEloss_trainbyUtility=criterion(PHI_dot_on_s_Utility.cpu()[:,-1],TARGET_test.reshape((-1,)))
        Utilityloss_trainbyUtility_on_s = Mean_Utility_on_s(XI_test_on_s.cpu(),PHI_on_s_Utility.cpu(),PHI_dot_on_s_Utility.cpu(),q,S_OUTSTANDING,GAMMA,LAM,MU_BAR,ALPHA,TIME)
        UtilitylossSQ_trainbyUtility_on_s = MeanSQ_Utility_on_s(XI_test_on_s.cpu(),PHI_on_s_Utility.cpu(),PHI_dot_on_s_Utility.cpu(),q,S_OUTSTANDING,GAMMA,LAM,MU_BAR,ALPHA,TIME)
        FBSDELOSS_Utility=FBSDELOSS_Utility +FBSDEloss_trainbyUtility
        mu_Utility =mu_Utility + Utilityloss_trainbyUtility_on_s
        mu2_Utility =mu2_Utility+ UtilitylossSQ_trainbyUtility_on_s 
        ### FBSDE
        FBSDEloss_trainbyFBSDE=criterion(torch.from_numpy(PHI_dot_FBSDE_on_s)[:,-1],TARGET_test.reshape((-1,)))
        Utilityloss_trainbyFBSDE_on_s = Mean_Utility_on_s(XI_test_on_s.cpu(),torch.from_numpy(PHI_FBSDE_on_s),torch.from_numpy(PHI_dot_FBSDE_on_s),q,S_OUTSTANDING,GAMMA,LAM,MU_BAR,ALPHA,TIME)
        UtilitylossSQ_trainbyFBSDE_on_s =MeanSQ_Utility_on_s(XI_test_on_s.cpu(),torch.from_numpy(PHI_FBSDE_on_s),torch.from_numpy(PHI_dot_FBSDE_on_s),q,S_OUTSTANDING,GAMMA,LAM,MU_BAR,ALPHA,TIME)
        FBSDELOSS_FBSDE=FBSDELOSS_FBSDE +FBSDEloss_trainbyFBSDE
        mu_FBSDE =mu_FBSDE+ Utilityloss_trainbyFBSDE_on_s
        mu2_FBSDE =mu2_FBSDE+ UtilitylossSQ_trainbyFBSDE_on_s 
        ### LO
        FBSDELoss_APP=criterion(torch.from_numpy(PHI_dot_APP_on_s)[:,-1], TARGET_test.reshape((-1,)))
        Utilityloss_LO_on_s = Mean_Utility_on_s(XI_test_on_s.cpu(),torch.from_numpy(PHI_APP_on_s),torch.from_numpy(PHI_dot_APP_on_s),q,S_OUTSTANDING,GAMMA,LAM,MU_BAR,ALPHA,TIME)
        UtilitylossSQ_LO_on_s = MeanSQ_Utility_on_s(XI_test_on_s.cpu(),torch.from_numpy(PHI_APP_on_s),torch.from_numpy(PHI_dot_APP_on_s),q,S_OUTSTANDING,GAMMA,LAM,MU_BAR,ALPHA,TIME)
        FBSDELOSS_LO=FBSDELOSS_LO +FBSDELoss_APP
        mu_LO =mu_LO+ Utilityloss_LO_on_s
        mu2_LO =mu2_LO+ UtilitylossSQ_LO_on_s        
        if itr==0:
            ###plot
            pathid=1#000
            fig = plt.figure(figsize=(10,4))
            time   = np.linspace(0, TIME_STEP*DT, TIME_STEP+1)
            time_FBSDE   = np.linspace(0, TIME_STEP*DT, TIME_STEP+1)
            ax1=plt.subplot(1,2,1)
            ax1.plot(time_FBSDE[1:],PHI_dot_FBSDE[pathid,:], label = "FBSDE")
            ax1.plot(time[1:],S_OUTSTANDING*PHI_dot_on_s_Utility[pathid,:].cpu().detach().numpy(), label = "Deep Hedging")
            ax1.plot(time[1:],S_OUTSTANDING*PHI_dot_APP_on_s[pathid,:], label = "Leading-order")
            ax1.hlines(0,xmin=0,xmax=TIME,linestyles='dotted')
            ax1.title.set_text("{}".format("$\\dot{\\varphi_t}$"))
            ax1.grid()
            ax2=plt.subplot(1,2,2)
            ax2.plot(time_FBSDE,PHI_FBSDE[pathid,:], label = "FBSDE")
            ax2.plot(time,S_OUTSTANDING*PHI_on_s_Utility[pathid,:].cpu().detach().numpy(), label = "Deep Hedging")
            ax2.plot(time,S_OUTSTANDING*PHI_APP_on_s[pathid,:], label = "Leading-order")
            ax2.title.set_text("{}".format("${\\varphi_t}$"))
            ax2.grid()
            box2=ax2.get_position()
            ax2.legend(loc="lower left", bbox_to_anchor=(box2.width*3,box2.height))
            plt.savefig(path+"q{} trading{}.pdf".format(q,TIME), bbox_inches='tight')
    big_result={"mu_Utility":mu_Utility ,
    "mu2_Utility":mu2_Utility ,
    "FBSDELOSS_Utility":FBSDELOSS_Utility, 
    "mu_FBSDE":mu_FBSDE ,
    "mu2_FBSDE":mu2_FBSDE, 
    "FBSDELOSS_FBSDE":FBSDELOSS_FBSDE ,
    "mu_LO":mu_LO,
    "mu2_LO":mu2_LO, 
    "FBSDELOSS_LO":FBSDELOSS_LO}
    return big_result

test_size=3#10000 later
REPEAT = 2 #10000 
torch.manual_seed(1)

n_cpu=1
batch_size = int(math.ceil(REPEAT / n_cpu))
big_result_arr = Parallel(n_jobs=n_cpu)(delayed(big_test)(
            test_size, min(REPEAT, batch_size * (i + 1)) - batch_size * i, model_list_Q
        ) for i in range(n_cpu))

big_result = big_result_arr[0]
for i in range(1, len(big_result_arr)):
    for key in big_result:
        big_result[key] += big_result_arr[i][key]

mu_Utility=big_result["mu_Utility"]
mu2_Utility =big_result["mu2_Utility"]
FBSDELOSS_Utility=big_result["FBSDELOSS_Utility"] 
mu_FBSDE=  big_result["mu_FBSDE"]
mu2_FBSDE=    big_result["mu2_FBSDE"] 
FBSDELOSS_FBSDE=    big_result["FBSDELOSS_FBSDE"]
mu_LO=    big_result["mu_LO"]
mu2_LO=    big_result["mu2_LO"] 
FBSDELOSS_LO=    big_result["FBSDELOSS_LO"]

FBSDELOSS_Utility=FBSDELOSS_Utility/ REPEAT
mu_Utility = mu_Utility/REPEAT
mu2_Utility = mu2_Utility/REPEAT
sigma_Utility = (mu2_Utility-mu_Utility**2)*(REPEAT*test_size)/(REPEAT*test_size-1)
sigma_Utility = sigma_Utility**0.5

FBSDELOSS_FBSDE=FBSDELOSS_FBSDE/ REPEAT
mu_FBSDE =mu_FBSDE/ REPEAT
mu2_FBSDE =mu2_FBSDE / REPEAT
sigma_FBSDE = (mu2_FBSDE-mu_FBSDE**2)*(REPEAT*test_size)/(REPEAT*test_size-1)
sigma_FBSDE = sigma_FBSDE **0.5

FBSDELOSS_LO=FBSDELOSS_LO/ REPEAT
mu_LO =mu_LO/ REPEAT
mu2_LO =mu2_LO/ REPEAT
sigma_LO = (mu2_LO-mu_LO**2)*(REPEAT*test_size)/(REPEAT*test_size-1)
sigma_LO=sigma_LO**0.5
#
mu_Utility = mu_Utility*S_OUTSTANDING
sigma_Utility=sigma_Utility*S_OUTSTANDING
mu_FBSDE = mu_FBSDE*S_OUTSTANDING
sigma_FBSDE=sigma_FBSDE*S_OUTSTANDING
mu_LO= mu_LO*S_OUTSTANDING
sigma_LO=sigma_LO*S_OUTSTANDING

df=pd.DataFrame(columns=["Method",'E(Utility)',"sd(Utility)","MSE at T (on S)"])
df=df.append({"Method":"Utility Based","E(Utility)":"{:e}".format(-mu_Utility.data.cpu().numpy()/TIME),"sd(Utility)":"{:e}".format(sigma_Utility.data.cpu().numpy()/TIME),"MSE at T (on S)":FBSDELOSS_Utility.data.cpu().numpy()},ignore_index=True)
df=df.append({"Method":"FBSDE","E(Utility)":"{:e}".format(-mu_FBSDE.data.cpu().numpy()/TIME),"sd(Utility)":"{:e}".format(sigma_FBSDE.data.cpu().numpy()/TIME),"MSE at T (on S)":FBSDELOSS_FBSDE.data.cpu().numpy()},ignore_index=True)
df=df.append({"Method":"Leading Order","E(Utility)":"{:e}".format(-mu_LO.data.cpu().numpy()/TIME),"sd(Utility)":"{:e}".format(sigma_LO.data.cpu().numpy()/TIME),"MSE at T (on S)":FBSDELOSS_LO.data.cpu().numpy()},ignore_index=True)

df.to_csv(path+"trading{}_cost{}.csv".format(TIME,q), index=False, header=True)