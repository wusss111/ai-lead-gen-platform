"""PyInstaller hook for sentence-transformers."""

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hiddenimports = collect_submodules("sentence_transformers")
datas = collect_data_files("sentence_transformers")
