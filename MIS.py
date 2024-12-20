import torch
from torch import Tensor
from PIL import Image
from metric import get_metric
import math


def get(set, x):
    return set[x]
get_I_subset1 = torch.vmap( get, in_dims=(None, 0) ) # for set: (n_samples , *I_dim) and x: (n_units, N), return: (n_units, N, *I_dim)
get_I_subset2 = torch.vmap(get_I_subset1, in_dims=(None, 0)) # for set: (n_samples , *I_dim) and x: (n_units, N, K+1), return: (n_units, N, K+1, *I_dim)
get_act_subset = torch.vmap( torch.vmap(get, in_dims=(None, 0)) ) # for set: (n_units, n_samples) and x: (n_units, N, K+1), return: (n_units, N, K+1, n_samples)
get_v = torch.vmap(get) 
get_vv = torch.vmap(get_v) # for set: (n_units, N, L) and x: (n_units, N, K+1), return: (n_units, N, K+1)


draw_k = torch.vmap(lambda x, L, k: torch.randperm(L)[:k], randomness="different",
                    in_dims=(0, None, None))

def subset_sampling(activations, K: int, N: int, quantile: float | int, activations_sort_id = None):
    n_units = activations.shape[0]
    n_samples = activations.shape[1]
    subset_length = math.ceil(n_samples * quantile)
    assert not subset_length < K+1

    if quantile==1:
        top_id = torch.stack([ draw_k(torch.empty(N), n_samples, K+1) 
                               for _ in range(n_units) ], dim=0)
        bottom_id = torch.stack([ draw_k(torch.empty(N), n_samples, K+1) 
                                  for _ in range(n_units) ], dim=0)
        return top_id , bottom_id

    if activations_sort_id is None:
        activations_sort_id = torch.argsort(activations, dim=-1, descending=False)

    top_set_id = torch.flip(activations_sort_id, [-1])[:, :subset_length]
    bottom_set_id = activations_sort_id[:, :subset_length]

    top_id = torch.stack([ draw_k(torch.empty(N), subset_length, K+1) 
                          for _ in range(n_units) ] , dim=0)
    bottom_id = torch.stack([ draw_k(torch.empty(N), subset_length, K+1)
                             for _ in range(n_units) ] , dim=0)
    
    top_id = get_act_subset(top_set_id , top_id)
    bottom_id = get_act_subset(bottom_set_id , bottom_id)

    return top_id , bottom_id


def sort_subset_id(top_id, bottom_id, activations):
    top_act = get_act_subset(activations, top_id)
    bottom_act = get_act_subset(activations, bottom_id)
    assert top_id.shape == bottom_id.shape
    assert top_act.shape == top_id.shape
    assert top_act.shape == bottom_act.shape

    top_sort_id = torch.argsort(top_act, dim=-1, descending=True)
    bottom_sort_id = torch.argsort(bottom_act, dim=-1, descending=False)

    top_id = get_vv(top_id, top_sort_id)
    bottom_id = get_vv(bottom_id, bottom_sort_id)

    return top_id, bottom_id


def query_explanation_generation(I_set, activations, K: int=9, N: int=20, quantile: float=0.2,
                                activations_sort_id=None):
    """
    Generate query and explanations for ALL psychophysics tasks.

    Input:
    - 'I_set': Torch tensor of preprocessed image dataset. Its first 
               dimension is length of the dataset, while subsequent dimensions depend on
               the model-specific input processing. For example, with Dreamsim
               preprocessing which converts a (WxHx3) image to a (1x1792) embedding, the
               dimensions of corresponding 'I_set' will be (ds_length x 1792).
               For LPIPS preprocess which recasts a (WxHx3) image to a (3xWxH) tensor,
               the tensor dimensions of corresponding 'I_set' will be (ds_length x W x H x 3).

    - 'activations': A two dimensional tensor (n_units x ds_length ) that
                     contains activations of every image along every unit.

    - 'K': The number of images in each (+ , -) explanation set per task.
    - 'N': Number of tasks per unit.
    - 'quantile': A scalar between 0 and 1. The quantile range over the activations for pschophysics query/explanation generation

    Internal:
    - 'I_dim': Dimensions of a single preprocessed image sample

    Output:
    - 'query_set': A tuple ('query_plus_set', 'query_minus_set') containing batched queries for all psychophysics tasks.
        - 'query_plus_set' , 'query_minus_set': torch tensor of shape (n_units, N, *I_dim)

    - 'Explanation_set': A tuple ('Explanation_plus_set', 'Explanation_minus_set') containing batched explanations for all psychophysics tasks.
        - 'Explanation_plus_set', 'Explanation_minus_set': torch tensor of shape (n_units, N, K, *I_dim)
    """
    top_id , bottom_id = subset_sampling(activations, K=K, N=N, quantile=quantile,
                                                            activations_sort_id=activations_sort_id)
    top_id , bottom_id = sort_subset_id(top_id, bottom_id, activations)

    Explanation_plus_set = get_I_subset2(I_set, top_id[:,:,:K])
    Explanation_minus_set = get_I_subset2(I_set, bottom_id[:,:,:K])

    query_plus_set = get_I_subset1(I_set, top_id[:,:,K])
    query_minus_set = get_I_subset1(I_set, bottom_id[:,:,K])

    query_set = (query_plus_set, query_minus_set)
    Explanation_set = (Explanation_plus_set, Explanation_minus_set)

    return query_set , Explanation_set


def aggregate(im_sim):
    a = torch.mean(im_sim, dim=-1)
    if a.shape[0]==1 and len(a.shape)==1:
        return a[0]
    return a
aggregate_batch = torch.vmap(aggregate)


def s(q_batch, E_batch, sim_metric_v):
    sim_batch = sim_metric_v(q_batch, E_batch) # similarity should be inverse relationship to distance metric
    a_batch = aggregate_batch(sim_batch)

    return a_batch


def calc_MIS(query, Explanation, sim_metric_v: callable, alpha: float|None=None):
    """
    Calculate Mechanistic Interpretability Score (MIS) for SINGLE UNIT

    Input:
    - 'query': A tuple ('q_plus', 'q_minus') containing queries of all psychophysics tasks for SINGLE UNIT.
        - 'q_plus', 'q_minus': Torch tensor of shape (N , *I_dim)

    - 'Explanation': A tuple ('E_plus' , 'E_minus') containing explanations of all psychophysics tasks for SINGLE UNIT.
        - 'E_plus', 'E_minus': Torch tensor of shape (N , K, *I_dim)

    - 'sim_metric': Callable similarity metric function.

    - 'alpha': Parametre for Sigmoid function in MIS calculation. If None, defaults to unnormalized psychophysics accuracy.

    Output:
    - 'MIS': Torch scalar of MIS of SINGLE UNIT

    """

    E_plus , E_minus = Explanation
    q_plus , q_minus = query

    assert q_plus.shape[0]==E_plus.shape[0]
    assert q_minus.shape[0]==E_minus.shape[0]
    assert q_plus.shape[0]==q_minus.shape[0]

    s_plus_plus = s(q_plus, E_plus, sim_metric_v)
    s_plus_minus = s(q_plus, E_minus, sim_metric_v)
    s_minus_plus = s(q_minus, E_plus, sim_metric_v)
    s_minus_minus = s(q_minus, E_minus, sim_metric_v)

    delta_plus = s_plus_plus - s_plus_minus
    delta_minus = s_minus_plus - s_minus_minus
    delta_difference = delta_plus - delta_minus

    if alpha is None:
        MIS = torch.sum(delta_difference > 0, dim=-1) / len(delta_difference)
        return MIS

    MIS = torch.sigmoid(alpha * delta_difference )
    MIS = torch.mean(MIS)
    return MIS


def calc_MIS_set(query_set, Explanation_set, sim_metric: callable, alpha=0.16):
    """
    Wrapper for callable 'calc_MIS' to compute MIS of ALL UNITS

    Input:
    - 'query_set': A tuple ('q_plus_set', 'q_minus_set') containing query processed images of every psychophysics task for ALL UNITS.
        - 'q_plus_set', 'q_minus_set': Torch tensor of shape (n_units, N , *I_dim)

    - 'Explanation_set': A tuple ('Explanation_plus_set', 'Explanation_minus_set') containing explanation processed images
                         of every psychophysics task for ALL UNITS.
        - 'Explanation_plus_set', 'Explanation_minus_set': Torch tensor of shape (n_units, N, K, *I_dim)

    - 'sim_metric': Callable similarity metric function to be passed into callable 'calc_MIS'

    - 'alpha': Parametre for Sigmoid function in MIS calculation, passed into callable 'calc_MIS'

    Output:
    - 'MIS_set': Torch tensor of shape (n_units,). Contains MIS of ALL UNITS.
    """

    assert len(query_set)==2
    assert len(Explanation_set)==2
    assert query_set[0].shape[0]==Explanation_set[0].shape[0]
    assert query_set[1].shape[0]==Explanation_set[1].shape[0]
    assert query_set[0].shape[0]==query_set[1].shape[0]

    (query_plus_set , query_minus_set) = query_set
    (Explanation_plus_set , Explanation_minus_set) = Explanation_set

    query_set = [ (q_plus , q_minus) for (q_plus , q_minus) in zip(query_plus_set , query_minus_set) ]
    Explanation_set = [ (E_plus , E_minus) for (E_plus , E_minus) in zip(Explanation_plus_set , Explanation_minus_set) ]

    MIS_set = torch.tensor([
        calc_MIS(query, Explanation, torch.vmap(sim_metric), alpha)
        for (query , Explanation) in zip(query_set , Explanation_set)
    ])

    return MIS_set

def run_psychophysics(I_set, activations, sim_metric: callable, K: int=9, N: int=20, quantile: float=0.2, alpha=0.16):
    
    query_set , Explanation_set = query_explanation_generation(I_set, activations, K=K, N=N, quantile=quantile)
    MIS_set = calc_MIS_set(query_set, Explanation_set, sim_metric, alpha=alpha)

    return MIS_set

class task_config:
    def __init__(self, image_set: list[Image], activations: Tensor,
                 device: str, processed: dict={}):

        self.x_data = image_set
        self.y_data = activations
        self.y_sort_id = torch.argsort(activations, dim=0, descending=False)
        self.device = device
        self.processed = processed

    def __getitem__(self, index):
        return self.x_data[index] , self.y_data[index]
    
    def get_data(self, metric_type: str=None):
        if metric_type is None:
            return self.X_data
        if metric_type in self.processed.keys():
            return self.processed[metric_type]
        
        self.processed[metric_type] = process(self.x_data, metric_type, self.device)
        return self.processed[metric_type]
    
    def get_target


def do_the_whole_thing(task_data: task_config, metric_type: str,
                       K: int, N: int, quantile: float, alpha: float=None):
    '''
    Input
        X: List of PIL Images (NxHXWXC)
        Y: Tensor (NxK)
    '''
    device = task_data.device

    I_set = task_data.get_data(metric_type=metric_type)
    activations = torch.transpose(torch.task_data.y_data, 0 ,1)
    activations_sort_id = torch.transpose(torch.task_data.y_sort_id, 0 ,1)
    sim_metric = get_metric(metric_type, device)

    query_set , Explanation_set = query_explanation_generation(I_set, activations, K=K, N=N, quantile=quantile, activations_id_sort=activations_sort_id)
    MIS_set = calc_MIS_set(query_set, Explanation_set, sim_metric, alpha=alpha)

    return MIS_set








