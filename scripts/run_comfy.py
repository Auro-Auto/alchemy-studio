#!/usr/bin/env python3
"""Run a ComfyUI API workflow using only Python's standard library.

Examples (quote paths containing spaces):
  python run_comfy.py --workflow workflow_api.json --output-dir outputs
  python run_comfy.py --workflow workflow_api.json --image-a "12=cat.png" --image-b "23=jellyfish.png"
  python run_comfy.py --workflow workflow_api.json --upload "12=cat.png"

Use ComfyUI's Export (API), not its ordinary UI workflow export. Image bindings
are explicit NODE_ID=LOCAL_FILE values; each file is uploaded before submission.
--upload also accepts a bare file path to upload without changing any node.
Each invocation creates a separate run directory containing PNGs and run.json.
A client timeout does not cancel the server's queued or running job.

API references:
https://github.com/Comfy-Org/ComfyUI/blob/master/script_examples/websockets_api_example.py
https://github.com/Comfy-Org/ComfyUI/blob/master/server.py
"""

from __future__ import annotations

import argparse
import json
import math
import mimetypes
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, parse, request


class ComfyError(Exception):
    def __init__(self, message, *, details=None, retryable=False):
        super().__init__(message)
        self.details = details
        self.retryable = retryable


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError("The total run timeout was reached.")
    return seconds


class ComfyAPI:
    def __init__(self, server, deadline):
        self.server = server.rstrip("/")
        self.deadline = deadline

    def fetch(self, path, *, data=None, headers=None, method="GET"):
        req = request.Request(self.server + path, data=data, headers=headers or {}, method=method)
        try:
            with request.urlopen(req, timeout=min(30.0, remaining(self.deadline))) as response:
                content = response.read()
            remaining(self.deadline)
            return content
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                details = json.loads(body)
            except ValueError:
                details = body
            raise ComfyError(
                f"{method} {path}: HTTP {exc.code} {exc.reason}\n{body}",
                details={"http_status": exc.code, "response": details},
                retryable=exc.code in (429, 502, 503, 504),
            ) from exc
        except (error.URLError, OSError) as exc:
            if time.monotonic() >= self.deadline:
                raise TimeoutError("The total run timeout was reached during an HTTP request.") from exc
            raise ComfyError(f"{method} {path}: {exc}", retryable=True) from exc

    def json(self, path, *, payload=None, data=None, headers=None):
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json"}
        content = self.fetch(path, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            result = json.loads(content)
        except (ValueError, UnicodeError) as exc:
            raise ComfyError(f"{path}: invalid JSON response", details=content.decode("utf-8", "replace")) from exc
        if not isinstance(result, dict):
            raise ComfyError(f"{path}: expected a JSON object", details=result)
        return result

    def upload(self, path):
        boundary = "alchemy-" + uuid.uuid4().hex
        # ASCII upload names avoid multipart header ambiguity and preserve local names in metadata.
        name = uuid.uuid4().hex + path.suffix.lower()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        parts = []
        for key, value in (("type", "input"), ("overwrite", "false")):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n".encode()
        )
        parts.extend((path.read_bytes(), f"\r\n--{boundary}--\r\n".encode()))
        result = self.json("/upload/image", data=b"".join(parts), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        if not isinstance(result.get("name"), str) or not result["name"]:
            raise ComfyError("Upload response has no file name", details=result)
        if result.get("type", "input") != "input":
            raise ComfyError("Upload was not stored in ComfyUI's input directory", details=result)
        if not isinstance(result.get("subfolder", ""), str):
            raise ComfyError("Upload response has an invalid subfolder", details=result)
        return result


def load_workflow(path):
    graph = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(graph, dict) and isinstance(graph.get("prompt"), dict):
        graph = graph["prompt"]
    if not isinstance(graph, dict) or not graph:
        raise ValueError("Workflow must be a nonempty API-format node dictionary.")
    for node_id, node in graph.items():
        if not isinstance(node, dict) or not isinstance(node.get("class_type"), str) or not isinstance(node.get("inputs"), dict):
            raise ValueError(f"Node {node_id!r} is not API format. Use ComfyUI's Export (API), not the UI workflow export.")
    return graph


def upload_bindings(args, graph):
    bindings = []
    seen = set()
    values = [("image-a", args.image_a), ("image-b", args.image_b)]
    values.extend(("upload", value) for value in args.upload)
    for role, value in values:
        if value is None:
            continue
        if role == "upload" and Path(value).is_file():
            node_id, file_name = None, value
        else:
            node_id, separator, file_name = value.partition("=")
            if not separator or not node_id or not file_name:
                raise ValueError(f"--{role} needs NODE_ID=LOCAL_FILE (or a bare file for --upload).")
            node = graph.get(node_id)
            if not node or node["class_type"] != "LoadImage":
                raise ValueError(f"--{role}: node {node_id!r} is not a LoadImage node.")
            if node_id in seen:
                raise ValueError(f"Multiple image arguments target node {node_id!r}.")
            seen.add(node_id)
        path = Path(file_name).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Input image does not exist: {path}")
        bindings.append((role, node_id, path))
    return bindings


def check_execution(entry):
    status = entry.get("status")
    if not isinstance(status, dict):
        raise ComfyError("History has no execution status; cannot confirm success.", details=entry)
    messages = status.get("messages", [])
    failures = [message for message in messages if isinstance(message, (list, tuple)) and message and message[0] in ("execution_error", "execution_interrupted")]
    if failures or status.get("status_str") == "error":
        raise ComfyError("ComfyUI execution failed:\n" + json.dumps(status, ensure_ascii=False, indent=2), details=status)
    if status.get("completed") is True:
        if status.get("status_str") != "success":
            raise ComfyError("History completed without a success status.", details=status)
        return True
    return False


def positive_seconds(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def parser():
    argparser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    argparser.add_argument("--server", default="http://127.0.0.1:8188", help="ComfyUI base URL (default: %(default)s)")
    argparser.add_argument("--workflow", required=True, type=Path, help="JSON exported using Export (API)")
    argparser.add_argument("--output-dir", type=Path, default=Path("outputs"), help="parent directory for this run's files (default: %(default)s)")
    argparser.add_argument("--timeout", type=positive_seconds, default=900.0, help="total seconds, including uploads and downloads (default: %(default)s)")
    argparser.add_argument("--poll-interval", type=positive_seconds, default=1.0, help="history polling seconds, capped at 60 (default: %(default)s)")
    argparser.add_argument("--image-a", metavar="NODE_ID=FILE", help="upload image A and bind this LoadImage node")
    argparser.add_argument("--image-b", metavar="NODE_ID=FILE", help="upload image B and bind this LoadImage node")
    argparser.add_argument("--upload", action="append", default=[], metavar="[NODE_ID=]FILE", help="upload an additional file, optionally binding a LoadImage node; repeatable")
    return argparser


def main(argv=None):
    args = parser().parse_args(argv)
    server = parse.urlsplit(args.server)
    if server.scheme not in ("http", "https") or not server.netloc or server.query or server.fragment:
        parser().error("--server must be an http(s) base URL without query or fragment")
    started = time.monotonic()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    run_dir = args.output_dir.expanduser().resolve() / run_id
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        print(f"Cannot create run directory: {exc}", file=sys.stderr)
        return 1
    metadata_path = run_dir / "run.json"
    metadata = {
        "started_at": utc_now(), "finished_at": None, "elapsed_seconds": None,
        "server": args.server.rstrip("/"), "status": "preparing", "prompt_id": None,
        "client_id": str(uuid.uuid4()), "workflow_path": str(args.workflow.resolve()),
        "workflow": None, "uploads": [], "outputs": [], "timeout_seconds": args.timeout,
    }

    def save_metadata():
        temp_path = metadata_path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(metadata_path)

    print(f"Run directory: {run_dir}", flush=True)
    exit_code = 1
    try:
        save_metadata()
        graph = load_workflow(args.workflow)
        metadata["workflow"] = graph
        bindings = upload_bindings(args, graph)
        api = ComfyAPI(args.server, started + args.timeout)
        for role, node_id, path in bindings:
            uploaded = api.upload(path)
            image_name = "/".join(part for part in (uploaded.get("subfolder", "").strip("/"), uploaded["name"]) if part)
            if node_id is not None:
                graph[node_id]["inputs"]["image"] = image_name
            metadata["uploads"].append({"role": role, "node_id": node_id, "local_path": str(path), "server_image": image_name, "response": uploaded})
            save_metadata()
            print(f"Uploaded {path.name}: {image_name}" + (f" -> node {node_id}" if node_id else ""), flush=True)

        metadata["status"] = "submitting"
        save_metadata()
        # Never retry POST /prompt: a lost response may still mean the job was queued.
        submitted = api.json("/prompt", payload={"prompt": graph, "client_id": metadata["client_id"]})
        metadata["submission"] = submitted
        prompt_id = submitted.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id or submitted.get("error"):
            raise ComfyError("ComfyUI did not accept the workflow:\n" + json.dumps(submitted, ensure_ascii=False, indent=2), details=submitted)
        metadata["prompt_id"] = prompt_id
        metadata["status"] = "waiting"
        save_metadata()
        print(f"Queued prompt: {prompt_id}", flush=True)
        if submitted.get("node_errors"):
            raise ComfyError("ComfyUI queued the prompt with node validation errors; refusing to report a partial workflow as successful.", details=submitted["node_errors"])
        while True:
            remaining(api.deadline)
            try:
                history = api.json("/history/" + parse.quote(prompt_id, safe=""))
            except ComfyError as exc:
                if not exc.retryable:
                    raise
                metadata["last_poll_error"] = str(exc)
                print(f"History temporarily unavailable: {exc}", file=sys.stderr, flush=True)
                time.sleep(min(args.poll_interval, 60.0, remaining(api.deadline)))
                continue
            entry = history.get(prompt_id)
            if entry is not None:
                if not isinstance(entry, dict):
                    raise ComfyError("Invalid history entry", details=entry)
                metadata["history"] = entry
                save_metadata()
                if check_execution(entry):
                    break
            time.sleep(min(args.poll_interval, 60.0, remaining(api.deadline)))

        metadata["status"] = "downloading"
        save_metadata()
        outputs = entry.get("outputs", {})
        if not isinstance(outputs, dict):
            raise ComfyError("History outputs must be a dictionary", details=outputs)
        for node_id, output in outputs.items():
            if not isinstance(output, dict) or not isinstance(output.get("images", []), list):
                raise ComfyError(f"Malformed output for node {node_id}", details=output)
            for index, image_info in enumerate(output.get("images", [])):
                if not isinstance(image_info, dict) or not isinstance(image_info.get("filename"), str):
                    raise ComfyError(f"Malformed image output for node {node_id}", details=image_info)
                filename = image_info.get("filename", "")
                if not filename.lower().endswith(".png"):
                    continue
                query = parse.urlencode({"filename": filename, "subfolder": image_info.get("subfolder", ""), "type": image_info.get("type", "output")})
                content = api.fetch("/view?" + query)
                if not content.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ComfyError(f"/view returned non-PNG content for {filename!r}; refusing to save it as PNG.")
                basename = re.sub(r"[^\w.\-]", "_", filename.replace("\\", "/").rsplit("/", 1)[-1])
                safe_node = re.sub(r"[^\w\-]", "_", str(node_id))
                local_path = run_dir / f"node_{safe_node}_{index:03d}_{basename}"
                local_path.write_bytes(content)
                metadata["outputs"].append({"node_id": node_id, "server_image": image_info, "local_path": str(local_path), "bytes": len(content)})
                save_metadata()
                print(f"Saved: {local_path}", flush=True)
        if not metadata["outputs"]:
            raise ComfyError("Execution succeeded but produced no PNG images. Add a SaveImage node to the API workflow.")
        metadata["status"] = "success"
        exit_code = 0
    except KeyboardInterrupt:
        metadata["status"] = "interrupted"
        metadata["error"] = "Interrupted by the client."
        exit_code = 130
    except (ComfyError, OSError, ValueError, TypeError) as exc:
        previous_status = metadata["status"]
        metadata["status"] = "timeout" if isinstance(exc, TimeoutError) else "failed"
        if previous_status == "submitting" and "submission" not in metadata:
            http_status = exc.details.get("http_status") if isinstance(exc, ComfyError) and isinstance(exc.details, dict) else None
            if http_status is None or http_status >= 500:
                metadata["submission_outcome"] = "unknown: the server may have accepted the prompt; no automatic retry was attempted"
            else:
                metadata["submission_outcome"] = "rejected"
        metadata["error"] = str(exc)
        if isinstance(exc, ComfyError) and exc.details is not None:
            metadata["error_details"] = exc.details
        print(f"Error: {exc}", file=sys.stderr, flush=True)
        exit_code = 124 if isinstance(exc, TimeoutError) else 1
    finally:
        metadata["finished_at"] = utc_now()
        metadata["elapsed_seconds"] = round(time.monotonic() - started, 3)
        try:
            save_metadata()
        except OSError as exc:
            print(f"Could not save run metadata: {exc}", file=sys.stderr)
            exit_code = 1
        print(f"Metadata: {metadata_path}", flush=True)
        if exit_code and (metadata["prompt_id"] or metadata.get("submission_outcome", "").startswith("unknown:")):
            print("The client did not cancel the server job. Check its history/queue before submitting again.", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
