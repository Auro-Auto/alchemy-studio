"""A controlled experiment: keep the same two references, seed and text; add a latent edit mask."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from build_workflow import build_api_workflow, node, write_json

ROOT = Path(__file__).resolve().parents[1]


def build_region_graph(prompt, seed=42, megapixels=0.5, masked=True):
    graph = build_api_workflow(prompt=prompt, seed=seed, megapixels=megapixels)
    # Save a VAE round-trip reference to separate encoding loss from edit leakage.
    graph['210'] = node('VAEDecode', 'Reference A reconstruction', samples=['116', 0], vae=['110', 0])
    graph['211'] = node('SaveImage', 'Reference reconstruction', images=['210', 0], filename_prefix='alchemy/region_reference')
    if masked:
        graph['201'] = node('LoadImage', 'Explicit edit mask', image='edit_mask.png')
        graph['202'] = node('ImageScale', 'Align edit mask', image=['201', 0], upscale_method='bilinear', width=['114', 0], height=['114', 1], crop='disabled')
        graph['203'] = node('ImageToMask', 'White is editable', image=['202', 0], channel='red')
        graph['204'] = node('SetLatentNoiseMask', 'Protect latent outside edit mask', samples=['116', 0], mask=['203', 0])
        graph['104']['inputs']['latent_image'] = ['204', 0]
    return graph


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',default='http://127.0.0.1:18888')
    parser.add_argument('--name',default='mug')
    parser.add_argument('--image-a',type=Path,default=ROOT/'input/plain_mug.jpg')
    parser.add_argument('--image-b',type=Path,default=ROOT/'input/real_cat.png')
    parser.add_argument('--rect',type=float,nargs=4,default=[0.28,0.48,0.70,0.90])
    parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--prompt',default='Redesign only the front surface of the white mug in image 1. Integrate one beautiful sculpted ceramic face of the black and white cat from image 2 into the mug body, preserving its distinctive white blaze, black forehead, green eyes and single pink nose. The face is a shallow glazed ceramic relief, not a flat photo. Keep exactly one face. Keep the existing mug silhouette, open rim, tea, handle, camera position and wooden table unchanged.')
    args=parser.parse_args()
    root=ROOT/'output/region_probe'/args.name
    root.mkdir(parents=True,exist_ok=True)
    with Image.open(args.image_a) as source:
        source=ImageOps.exif_transpose(source).convert('RGB')
        mask=Image.new('L',source.size,0)
        x0,y0,x1,y1=args.rect
        ImageDraw.Draw(mask).rounded_rectangle((int(x0*source.width),int(y0*source.height),int(x1*source.width),int(y1*source.height)),radius=int(min(source.size)*.05),fill=255)
        mask=mask.filter(ImageFilter.GaussianBlur(max(1,source.width*.002)))
        mask.convert('RGB').save(root/'edit_mask.png')
    records=[]
    for method in ['global','latent_mask']:
        graph=build_region_graph(args.prompt,args.seed,masked=method=='latent_mask')
        graph_path=root/f'{method}_api.json'
        write_json(graph_path,graph)
        command=[sys.executable,str(ROOT/'scripts/run_comfy.py'),'--server',args.server,'--workflow',str(graph_path),'--image-a',f'76={args.image_a.resolve()}','--image-b',f'81={args.image_b.resolve()}','--output-dir',str(root/method),'--poll-interval','1']
        if method=='latent_mask':command+=['--upload',f'201={root/"edit_mask.png"}']
        subprocess.run(command,check=True)
        records.append({'method':method,'workflow':str(graph_path),'rect':args.rect,'seed':args.seed,'prompt':args.prompt})
    write_json(root/'protocol.json',records)


if __name__=='__main__':main()
