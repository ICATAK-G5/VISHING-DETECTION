# Python backend environment

The project Python environment lives in `.venv` in this folder. It uses Python 3.12.10.

In PowerShell, activate it from the project root:

```powershell
.\backend\.venv\Scripts\Activate.ps1
```

Install or restore the recorded packages with:

```powershell
python -m pip install -r .\backend\requirements.txt
```

Leave `.venv` out of Git; `requirements.txt` records the packages instead. FFmpeg is installed separately on Windows and available through the user's PATH in newly opened terminals.

PyTorch is currently installed as the CPU build. The NVIDIA RTX 3050 Ti is available, but the official CUDA wheel was about 2 GB; the download was stopped after transferring only a small fraction. We can switch the environment to a CUDA build later if the selected models benefit from GPU acceleration.
