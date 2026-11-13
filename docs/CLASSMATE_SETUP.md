# 同学在 Windows 游戏本上安装

目前的正式复现路径是 Windows + WSL2 Ubuntu 22.04 + NVIDIA GPU，或原生 Linux。下载源码后，需要先安装环境和模型。当前启动器使用 Linux 路径。

## 已经验证到哪一步

| 检查 | 结果 |
| --- | --- |
| RTX 5070 Ti 16GB、WSL2 实际生成 | 既有部署已验证，演示图来自该环境 |
| 独立 Ubuntu 22.04 / Python 3.10 应用依赖安装 | 2026-11-13 验证通过 |
| 独立环境 CPU 测试 | 70 项通过、1 项跳过、5 个子测试通过；跳过项需要 PyTorch |
| Linux Node.js 22.23.5 的全新 npm ci 与前端构建 | 验证通过 |
| 全新环境 U2Netp 下载、校验与真实马克杯抠图 | 验证通过 |
| RTX 4060 / 8GB 等同学的笔记本 | 尚未实机验证 |
| Windows 原生启动 | 当前安装脚本走 WSL2 |

因此还不能保证任意游戏本一次安装成功。8GB 显存、系统内存、驱动和模型下载网络都需要在具体机器检查。

## 1. 准备 WSL2 和 GPU

Windows 管理员 PowerShell 中运行，按提示完成安装并重启。

~~~powershell
wsl --install -d Ubuntu-22.04
wsl --update
wsl --list --verbose
~~~

Ubuntu-22.04 的 VERSION 应为 2。安装适合自己显卡的 Windows NVIDIA 驱动，然后打开 Ubuntu 终端运行 nvidia-smi，确认能看到显卡。

WSL 的 GPU 使用 Windows 主机驱动，按 [NVIDIA 官方 WSL 指南](https://docs.nvidia.com/cuda/wsl-user-guide/index.html) 配置。缺少 GPU 或驱动时，先解决环境问题再运行安装器。

建议主机有 32GB 系统内存；16GB 主机可尝试，但 WSL 分配较少内存时，CPU 卸载可能使内存不足。查看 WSL 内可用内存和项目磁盘空间。

~~~bash
free -h
df -h .
~~~

安装器要求安装前至少 25GiB 空闲磁盘，大模型下载、Torch/CUDA 依赖、缓存和输出都会继续占空间。

## 2. 安装 Linux 内的工具

以下命令都在 Ubuntu 终端运行。

~~~bash
sudo apt update
sudo apt install -y python3.10 python3.10-venv git curl xz-utils build-essential
~~~

前端需要 Linux Node.js 22.12+。可使用 [Node.js 官方安装说明](https://nodejs.org/en/download)，也可为本项目安装已指定的 x64 二进制版本。下面的 Node 路径放在自己的 home 下。

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

新开终端后需要再次设置 PATH，也可以把上面的 export 行加入自己的 ~/.bashrc。不要仅依赖 Windows 中安装的 npm；WSL 需要可以运行的 Linux node。

## 3. 获取源码并执行统一安装

~~~bash
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/Auro-Auto/alchemy-studio.git
cd alchemy-studio

python3.10 scripts/check_environment.py
bash scripts/install.sh
~~~

也可以在 GitHub 用 Code → Download ZIP，解压后进入项目目录执行最后两行。建议源码放在 WSL 的 home 文件系统。

安装器依次检查系统、Python、磁盘、Node 和 NVIDIA 驱动，安装配套的 CUDA PyTorch / torchvision，校验实际 CUDA 分配，然后固定 ComfyUI 版本、下载已有三份生成权重、安装分割与应用依赖、运行测试并构建前端。中途报错会停止，修复问题后可重跑。

Torch 2.11.0 与 torchvision 0.26.0 从同一 cu128 源安装，配套关系见 [PyTorch 官方版本安装表](https://pytorch.org/get-started/previous-versions/#v2110)。

模型下载约 8.26GB，另外会安装 Torch/CUDA 包。项目使用的模型和原演示相同。下载器检查文件大小与哈希。镜像失败或校验错误时，按 [REPRODUCTION.md](../REPRODUCTION.md) 核对文件与备用来源。

## 4. 启动和第一张图

~~~bash
.venv/bin/python scripts/check_environment.py --ready
.venv/bin/python scripts/start_studio.py
~~~

Windows 浏览器打开 http://localhost:8890。选择“早八的第一口”，先用 0.5 MP、1 个候选测试，再尝试自己的图片。

根目录的“启动万物炼金台.cmd”沿用远程 SSH 演示入口，需要自己配置 SSH 别名。本机安装请使用本节的 WSL 启动命令。

正常应完成一次生成，并可以打开结果、下载 PNG、从历史恢复。单纯页面打开或自检通过，仍需用这次实际生成确认 GPU 推理链路。

## 5. 8GB 显卡的低显存尝试

这套配置尚未在 8GB GPU 上实测。先使用 0.5 MP、1 个候选；候选本来就串行执行。关闭占用显存的其他程序。

如果默认启动发生显存不足，可以在 Studio 没有活动任务、现有 ComfyUI 已停止的情况下，先在一个 Ubuntu 终端以前台方式启动以下配置。

~~~bash
cd ~/projects/alchemy-studio
cd backend/ComfyUI
../../.venv/bin/python main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch --disable-api-nodes --reserve-vram 1.0 --disable-dynamic-vram --lowvram --cpu-vae
~~~

保持该终端开启，在另一个 Ubuntu 终端执行 scripts/start_studio.py。

~~~bash
cd ~/projects/alchemy-studio
.venv/bin/python scripts/start_studio.py
~~~

这些是 [固定版本 ComfyUI 的参数](https://github.com/Comfy-Org/ComfyUI/blob/bdb0816a292d221efeb6ec4ae41bcdff3f91fc86/comfy/cli_args.py)。该版本的 lowvram 在动态显存开启时不生效，所以这里同时关闭动态显存，并把 VAE 放到 CPU。这样可能更慢，也会增加系统内存压力。该启动方式需要同学实际出图确认，不能视为 8GB 兼容性保证。

## 排错时保留什么

- 自检的完整输出，以及显卡型号、显存、WSL 内存。
- 安装失败的完整错误，模型下载失败时包括具体文件名。
- 推理失败时的 logs/comfyui.log、logs/studio.log 和工作台错误信息。
- 本次测试的分辨率、候选数、输入图与实际耗时。

个人上传与任务记录保存在 data/，分享排错文件前确认其中内容适合公开。
