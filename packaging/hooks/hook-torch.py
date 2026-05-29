"""PyInstaller hook for torch — force CPU-only, strip CUDA libs (~1.5 GB saved)."""

from PyInstaller.utils.hooks import collect_submodules, collect_dynamic_libs

hiddenimports = collect_submodules("torch")

binaries = collect_dynamic_libs("torch")
binaries = [
    (src, dst)
    for src, dst in binaries
    if "cuda" not in src.lower()
    and "cudnn" not in src.lower()
    and "cublas" not in src.lower()
    and "cufft" not in src.lower()
    and "cusparse" not in src.lower()
    and "nvidia" not in src.lower()
    and "nccl" not in src.lower()
]
