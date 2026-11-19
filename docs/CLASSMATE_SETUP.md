# Windows 安装

项目在 Windows 的 WSL2 Ubuntu 22.04 中运行，也支持原生 Linux。安装器使用 Linux 路径，需要 NVIDIA GPU。

## 环境要求

| 项目 | 配置 |
| --- | --- |
| 系统 | WSL2 Ubuntu 22.04 或原生 Linux |
| Python | 3.10 |
| 前端 | Linux Node.js 22.12+、npm |
| 磁盘 | 安装前至少 25GiB 空闲空间 |
| 内存 | 建议 32GB；16GB 主机使用 CPU 卸载时可能不足 |
| 受测 GPU | RTX 5070 Ti 16GB |
| 8GB GPU | 尚未实机验证 |

## 1. 安装 WSL2

在管理员 PowerShell 中运行，按提示重启：

~~~powershell
wsl --install -d Ubuntu-22.04
wsl --update
wsl --list --verbose
~~~

确认 Ubuntu-22.04 的 VERSION 为 2。安装适合显卡的 Windows NVIDIA 驱动，然后在 Ubuntu 中运行 `nvidia-smi`，确认能看到 GPU。WSL 使用 Windows 主机驱动，配置见 [NVIDIA WSL 指南](https://docs.nvidia.com/cuda/wsl-user-guide/index.html)。

检查 WSL 内存和磁盘：

~~~bash
free -h
df -h .
~~~

## 2. 安装工具

在 Ubuntu 终端运行：

~~~bash
sudo apt update
sudo apt install -y python3.10 python3.10-venv git curl xz-utils build-essential
~~~

按 [Node.js 安装文档](https://nodejs.org/en/download) 安装 Linux 版本，或使用以下 x64 二进制包：

~~~bash
mkdir -p ~/alchemy-tools
cd ~/alchemy-tools
NODE_VERSION=v22.23.5
curl -fLO "https://nodejs.org/dist/$NODE_VERSION/node-$NODE_VERSION-linux-x64.tar.xz"
curl -fLO "https://nodejs.org/dist/$NODE_VERSION/SHASUMS256.txt"
sha256sum --check --ignore-missing SHASUMS256.txt
mkdir -p "$HOME/.local/node22"
tar -xJf "node-$NODE_VERSION-linux-x64.tar.xz" --strip-components=1 -C "$HOME/.local/node22"
export PATH="$HOME/.local/node22/bin:$PATH"
node --version
npm --version
~~~

将 `export PATH=...` 一行加入 `~/.bashrc`，使新终端也能找到 Node。WSL 中需要 Linux Node，不能仅使用 Windows 安装的 npm。

## 3. 安装项目

~~~bash
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/Auro-Auto/alchemy-studio.git
cd alchemy-studio
python3.10 scripts/check_environment.py
bash scripts/install.sh
~~~

也可使用 GitHub 的 Code → Download ZIP，解压到 WSL 文件系统，再执行最后两条命令。

安装器检查系统、Python、磁盘、Node 和 NVIDIA 驱动，安装 CUDA PyTorch / torchvision，验证 CUDA 分配，固定 ComfyUI 版本，下载并校验模型，安装应用依赖，最后运行测试与前端构建。报错时会停止，修复后可重新运行。

PyTorch 2.11.0 与 torchvision 0.26.0 使用 cu128 源，配套版本见 [PyTorch 安装表](https://pytorch.org/get-started/previous-versions/#v2110)。生成权重约 8.26GB，Torch/CUDA 包另占空间。下载失败或校验错误时，按 [安装指南](../REPRODUCTION.md) 核对文件与备用来源。

## 4. 启动

~~~bash
.venv/bin/python scripts/check_environment.py --ready
.venv/bin/python scripts/start_studio.py
~~~

Windows 浏览器打开 `http://localhost:8890`。选择“早八的第一口”，使用 0.5MP、1 个候选生成，再检查图片下载与历史记录。

根目录的“启动万物炼金台.cmd”用于远程 SSH 演示，需要配置 SSH 别名。本机安装使用上述 WSL 命令。

## 5. 低显存配置

8GB GPU 尚未实测。可先设为 0.5MP、1 个候选，并关闭其他占用显存的程序。候选按顺序生成。

默认配置出现显存不足时，等待 Studio 活动任务结束、停止已有 ComfyUI，在一个 Ubuntu 终端运行：

~~~bash
cd ~/projects/alchemy-studio
cd backend/ComfyUI
../../.venv/bin/python main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch --disable-api-nodes --reserve-vram 1.0 --disable-dynamic-vram --lowvram --cpu-vae
~~~

保持该终端开启，在另一个 Ubuntu 终端启动 Studio：

~~~bash
cd ~/projects/alchemy-studio
.venv/bin/python scripts/start_studio.py
~~~

固定版本的 `lowvram` 需要关闭动态显存，因此同时设置 `--disable-dynamic-vram`；`--cpu-vae` 将 VAE 放到 CPU。这会增加系统内存压力并可能降低速度。参数定义见 [ComfyUI cli_args.py](https://github.com/Comfy-Org/ComfyUI/blob/bdb0816a292d221efeb6ec4ae41bcdff3f91fc86/comfy/cli_args.py)。

## 排错

记录显卡型号、显存、WSL 内存、自检输出及完整错误。推理日志位于 `logs/comfyui.log`、`logs/studio.log`；模型下载错误需同时记录文件名。图片与任务数据保存在 `data/`，分享日志前检查是否包含个人上传内容。
