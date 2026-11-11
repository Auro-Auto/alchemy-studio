"""Measure sampled NVIDIA VRAM while running a benchmark command.

Use --pid for the already running ComfyUI process when the command is an
HTTP client. Device values cover the entire card. Missing measurements are
null; the sampled peak is a lower bound on the instantaneous peak.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import shutil
from statistics import median
import subprocess
import sys
from tempfile import NamedTemporaryFile
from time import monotonic


BYTES_PER_MIB = 1024**2


def _mib(value: str) -> int | None:
    try:
        amount = int(value.strip())
        return amount if amount >= 0 else None
    except ValueError:
        return None


class NvidiaSmi:
    source = "nvidia-smi"

    def __init__(self, index: int):
        self.index = index
        executable = shutil.which("nvidia-smi")
        wsl_executable = Path("/usr/lib/wsl/lib/nvidia-smi")
        if executable is None and wsl_executable.is_file():
            executable = str(wsl_executable)
        if executable is None:
            raise FileNotFoundError("nvidia-smi was not found")
        self.executable = executable
        (name, uuid, total), = self._query("gpu", "name,uuid,memory.total")
        self.device = {"index": index, "name": name, "uuid": uuid, "total_mib": _mib(total)}

    def _query(self, kind: str, fields: str) -> list[list[str]]:
        result = subprocess.run(
            [self.executable, "-i", str(self.index), f"--query-{kind}={fields}", "--format=csv,noheader,nounits"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=3,
        )
        if result.returncode:
            message = (result.stderr or result.stdout).strip()
            raise RuntimeError(message or f"nvidia-smi exited with {result.returncode}")
        return [row for row in csv.reader(result.stdout.splitlines(), skipinitialspace=True) if row]

    def device_used(self) -> int | None:
        (used,), = self._query("gpu", "memory.used")
        return _mib(used)

    def process_used(self, pid: int) -> int | None:
        for row in self._query("compute-apps", "pid,used_gpu_memory"):
            if len(row) == 2 and row[0].strip() == str(pid):
                return _mib(row[1])
        return None

    def close(self) -> None:
        pass


class Nvml:
    source = "nvml"

    def __init__(self, index: int):
        import pynvml

        self.api = pynvml
        self.api.nvmlInit()
        try:
            self.handle = self.api.nvmlDeviceGetHandleByIndex(index)
            name = self.api.nvmlDeviceGetName(self.handle)
            uuid = self.api.nvmlDeviceGetUUID(self.handle)
            self.device = {
                "index": index,
                "name": name.decode() if isinstance(name, bytes) else name,
                "uuid": uuid.decode() if isinstance(uuid, bytes) else uuid,
                "total_mib": round(self.api.nvmlDeviceGetMemoryInfo(self.handle).total / BYTES_PER_MIB, 3),
            }
        except Exception:
            self.close()
            raise

    def device_used(self) -> float:
        return round(self.api.nvmlDeviceGetMemoryInfo(self.handle).used / BYTES_PER_MIB, 3)

    def process_used(self, pid: int) -> float | None:
        for process in self.api.nvmlDeviceGetComputeRunningProcesses(self.handle):
            if process.pid == pid:
                used = process.usedGpuMemory
                # NVML may mark the process value unavailable on WSL/WDDM.
                if used is not None and 0 <= used < (2**64 - 1):
                    return round(used / BYTES_PER_MIB, 3)
                return None
        return None

    def close(self) -> None:
        self.api.nvmlShutdown()


def _open_sampler(index: int, errors: list[str]):
    for kind in (Nvml, NvidiaSmi):
        try:
            return kind(index)
        except Exception as error:
            errors.append(f"{kind.source} initialization: {error}")
    return None


def _record_error(errors: list[str], message: str) -> None:
    if message not in errors:
        errors.append(message)


def _snapshot(sampler, pid: int | None, errors: list[str]) -> dict:
    device = process = None
    if sampler is not None:
        try:
            device = sampler.device_used()
        except Exception as error:
            _record_error(errors, f"{sampler.source} device sampling: {error}")
        if pid is not None:
            try:
                process = sampler.process_used(pid)
            except Exception as error:
                _record_error(errors, f"{sampler.source} process sampling: {error}")
    return {"device_used_mib": device, "process_used_mib": process}


def _usage(baseline: dict, samples: list[dict], key: str) -> dict:
    values = [sample[key] for sample in samples if sample[key] is not None]
    idle = baseline[key]
    peak = max(values, default=None)
    return {
        "baseline_mib": idle,
        "peak_mib": peak,
        "peak_over_baseline_mib": round(peak - idle, 3) if peak is not None and idle is not None else None,
        "sample_count": len(values),
    }


def _intervals(samples: list[dict]) -> dict | None:
    intervals = [b["elapsed_seconds"] - a["elapsed_seconds"] for a, b in zip(samples, samples[1:])]
    if not intervals:
        return None
    return {"min": round(min(intervals), 4), "median": round(median(intervals), 4), "max": round(max(intervals), 4)}


def measure(command: list[str], gpu: int, pid: int | None, interval: float) -> dict:
    errors: list[str] = []
    sampler = _open_sampler(gpu, errors)
    baseline = _snapshot(sampler, pid, errors)
    samples: list[dict] = []
    child = None
    returncode = None
    started = monotonic()
    try:
        child = subprocess.Popen(command)
        if sampler is None:
            returncode = child.wait()
        else:
            while child.poll() is None:
                sample_time = monotonic()
                samples.append({"elapsed_seconds": round(sample_time - started, 6), **_snapshot(sampler, pid, errors)})
                try:
                    returncode = child.wait(timeout=max(0, sample_time + interval - monotonic()))
                    break
                except subprocess.TimeoutExpired:
                    pass
            if returncode is None:
                returncode = child.wait()
    except OSError as error:
        errors.append(f"child command: {error}")
    except KeyboardInterrupt:
        errors.append("interrupted by keyboard")
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        returncode = 130
    elapsed = monotonic() - started
    if sampler is not None:
        if child is not None:
            samples.append({"elapsed_seconds": round(elapsed, 6), **_snapshot(sampler, pid, errors)})
        try:
            sampler.close()
        except Exception as error:
            _record_error(errors, f"{sampler.source} shutdown: {error}")

    device = _usage(baseline, samples, "device_used_mib")
    process = _usage(baseline, samples, "process_used_mib") if pid is not None else None
    if device["peak_mib"] is None:
        _record_error(errors, "No device memory samples were reported during the command")
    if process is not None and process["peak_mib"] is None:
        _record_error(errors, f"No compute memory samples were reported for PID {pid}")
    return {
        "command": command,
        "gpu": sampler.device if sampler is not None else {"index": gpu},
        "sampling_source": sampler.source if sampler is not None else None,
        "requested_interval_seconds": interval,
        "observed_sample_intervals_seconds": _intervals(samples),
        "process_pid": pid,
        "elapsed_seconds": round(elapsed, 6),
        "returncode": returncode,
        "device": device,
        "process": process,
        "samples": samples,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="JSON report path")
    parser.add_argument("--gpu", type=int, default=0, help="physical GPU index (default: 0)")
    parser.add_argument("--pid", type=int, help="optional ComfyUI server PID")
    parser.add_argument("--interval", type=float, default=.1, help="requested sample interval in seconds (default: 0.1)")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="command to measure, after --")
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("supply a command after --")
    if args.gpu < 0 or (args.pid is not None and args.pid < 1):
        parser.error("GPU index must be nonnegative and PID must be positive")
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error("interval must be positive and finite")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = measure(command, args.gpu, args.pid, args.interval)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent, delete=False) as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    temporary = Path(stream.name)
    try:
        temporary.replace(args.output)
    finally:
        temporary.unlink(missing_ok=True)
    code = report["returncode"]
    if code is None:
        return 1
    return 128 - code if code < 0 else code


if __name__ == "__main__":
    sys.exit(main())
