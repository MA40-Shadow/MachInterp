import torch

def get_lpips(device):
    import lpips
    from torchvision.transforms import ToTensor

    loss_fn = lpips.LPIPS(net='alex')
    single_convert = ToTensor()

    if device == 'cuda':
        loss_fn.cuda()

    def sim_metric(im_tensor0, im_tensor1):
        assert len(im_tensor0.shape) in (3, 4)
        assert len(im_tensor1.shape) in (3, 4)
        if len(im_tensor0.shape) == 3:
            im_tensor0 = im_tensor0.unsqueeze(0)
        if len(im_tensor1.shape) == 3:
            im_tensor1 = im_tensor1.unsqueeze(0)

        output = torch.zeros(im_tensor0.shape[0], im_tensor1.shape[0], device=device)

        with torch.no_grad():
            for ii, im0 in enumerate(im_tensor0):
                for jj, im1 in enumerate(im_tensor1):
                    output[ii, jj] = - loss_fn.foward(im0.to(device), im1.to(device).item())

        return output

    def preprocess(im):
        return single_convert(im).unsqueeze(0)

    def preprocess_ds(ds, collate_fn: callable):
        from torch.utils.data import DataLoader
        from functools import partial
        collate_fn_ds = partial(collate_fn, preprocess=preprocess)
        ds_loader = DataLoader(ds, batch_size=64,
                               shuffle=False, num_workers=2*torch.cuda.device_count(),
                               collate_fn=collate_fn_ds)

        try:
            from tqdm.notebook import tqdm
            loader_loop = tqdm(enumerate(ds_loader), total=len(ds_loader) )
        except ImportError:
            loader_loop = enumerate(ds_loader)

        im_tensor_set=[]
        for _ , X in loader_loop:
            im_tensor_set.append( X.to(device) )

        return torch.cat(im_tensor_set, dim=0)


    return sim_metric , preprocess_ds


def get_dreamsim(device):
    from dreamsim import dreamsim
    import torch.nn.functional as F

    model, preprocess = dreamsim(pretrained=True, device=device)

    embed_fn = model.embed

    def sim_metric(embed1, embed2):
        if len(embed1.shape)==1:
            embed1 = embed1.unsqueeze(0)
        if len(embed2.shape)==1:
            embed2 = embed2.unsqueeze(0)

        return F.cosine_similarity(embed1[:, None], embed2[None], dim=-1)

    def preprocess_embed_ds(ds, collate_fn: callable):
        from torch.utils.data import DataLoader
        from functools import partial
        collate_fn_ds = partial(collate_fn, preprocess=preprocess)
        ds_loader = DataLoader(ds, batch_size=64,
                               shuffle=False, num_workers=2*torch.cuda.device_count(),
                               collate_fn=collate_fn_ds)

        try:
            from tqdm.notebook import tqdm
            loader_loop = tqdm(enumerate(ds_loader), total=len(ds_loader) )
        except ImportError:
            loader_loop = enumerate(ds_loader)

        embedding=[]
        with torch.no_grad():
            for _ , X in loader_loop:
                embedding.append( embed_fn(X.to(device)) )

        return torch.cat(embedding, dim=0)

    return sim_metric, preprocess_embed_ds

def get_metric(metric_type: str, device=torch.device("cuda" if torch.cuda.is_available() else 'cpu')):
    metric_type = metric_type.lower()
    assert metric_type in ["dreamsim", "lpips"]
    device = device.lower()
    assert device in ['cpu', 'cuda']

    if metric_type == "dreamsim":
        return get_dreamsim(device)
    if metric_type == "lpips":
        return get_dreamsim(device)
