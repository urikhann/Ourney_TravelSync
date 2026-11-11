"""Explicit public checkpoint download; no POI or participant data is transmitted."""
import os
import json
from pathlib import Path

os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN']='1'
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
from huggingface_hub import snapshot_download

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
if __name__=='__main__':
    for model in json.loads((HERE/'config.json').read_text())['models']:
        path=snapshot_download(model['id'],revision=model['revision'],cache_dir=ROOT/model['cache'],
            token=False,allow_patterns=['*.json','*.txt','*.safetensors','*.model','README.md','1_Pooling/*'],
            ignore_patterns=['onnx/*','openvino/*'])
        print(model['id'],model['revision'],path)
