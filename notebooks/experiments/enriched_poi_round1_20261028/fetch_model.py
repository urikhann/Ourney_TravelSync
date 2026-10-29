"""Download the pinned public model only; never send scenario/place text to a service."""
import os
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
from pathlib import Path
import json
import urllib.request
from prepare import MODEL, HERE

if __name__ == "__main__":
    from huggingface_hub import snapshot_download
    cache = HERE / ".model-cache"
    path = snapshot_download(MODEL["id"], revision=MODEL["revision"], cache_dir=cache,
                             token=False, allow_patterns=["*.json", "*.txt", "*.safetensors", "README.md", "1_Pooling/*"],
                             ignore_patterns=["onnx/*", "openvino/*"])
    metadata = json.load(urllib.request.urlopen("https://huggingface.co/api/models/" + MODEL["id"] + "/revision/" + MODEL["revision"], timeout=30))
    assert metadata["sha"] == MODEL["revision"]
    assert "ko" in metadata["cardData"]["language"]
    print(json.dumps({"snapshot": path, "revision": metadata["sha"], "license": metadata["cardData"]["license"]}))
