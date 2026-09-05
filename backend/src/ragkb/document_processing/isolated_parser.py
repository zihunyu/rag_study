"""Native parsers run outside the Worker with wall/CPU and output-size limits."""

from __future__ import annotations

import json
import multiprocessing
import os
import sys
import tempfile
from multiprocessing.connection import Connection
from pathlib import Path

from pydantic import TypeAdapter

from ragkb.contracts.ports import ParsingDeferred
from ragkb.domain.documents import CanonicalDocument
from ragkb.engineering_security.process_limits import limit_native_process


def _parse(
    source_format: str, path: str, version_id: str, sender: Connection, timeout: float, workdir: str
) -> None:
    try:
        limit_native_process(timeout)
        os.chdir(workdir)
        os.environ.update(TMP=workdir, TEMP=workdir, TMPDIR=workdir)
        tempfile.tempdir = workdir
        sys.dont_write_bytecode = True

        def deny_network(event: str, args: tuple[object, ...]) -> None:
            if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "subprocess.Popen"}:
                raise PermissionError("NATIVE_PARSER_NETWORK_OR_PROCESS_DENIED")

        sys.addaudithook(deny_network)
        from ragkb.document_processing.parsers import ParserRouter

        document = ParserRouter().route(source_format).parse(Path(path), version_id)
        payload = json.dumps({"document": document.to_dict()}, ensure_ascii=False).encode()
        if len(payload) > 64 * 1024**2:
            raise ParsingDeferred("PARSE_OUTPUT_LIMIT", "native parser output exceeds 64 MiB")
        sender.send_bytes(payload)
    except ParsingDeferred as error:
        sender.send_bytes(json.dumps({"error": error.code}).encode())
    except BaseException:
        sender.send_bytes(b'{"error":"NATIVE_PARSE_FAILED"}')
    finally:
        sender.close()


class IsolatedNativeParser:
    revision = "isolated-native-parser:v1"

    def __init__(self, source_format: str, timeout: float = 90) -> None:
        self.source_format, self.timeout = source_format, timeout

    def parse(self, source: Path, document_version_id: str) -> CanonicalDocument:
        context = multiprocessing.get_context("spawn")
        temporary = tempfile.TemporaryDirectory(prefix="rag-native-parser-")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(
            target=_parse,
            args=(
                self.source_format,
                str(source.resolve()),
                document_version_id,
                sender,
                self.timeout,
                temporary.name,
            ),
            daemon=True,
        )
        try:
            process.start()
            sender.close()
            if not receiver.poll(self.timeout):
                raise ParsingDeferred("NATIVE_PARSE_TIMEOUT", "native parser timed out")
            payload = json.loads(receiver.recv_bytes(maxlength=64 * 1024**2))
            if "error" in payload:
                raise ParsingDeferred(payload["error"], "native parser could not process content")
            document = payload["document"]
            for node in document["nodes"]:
                node["node_type"] = node.pop("type")
            return TypeAdapter(CanonicalDocument).validate_python(document)
        except (EOFError, OSError, ValueError) as error:
            raise ParsingDeferred("NATIVE_PARSE_FAILED", "invalid native parser result") from error
        finally:
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(1)
                if process.is_alive():
                    process.kill()
                    process.join(1)
                process.close()
            receiver.close()
            sender.close()
            temporary.cleanup()


class UnconfiguredASRParser:
    revision = "asr-unconfigured:v1"

    def parse(self, source: Path, document_version_id: str) -> CanonicalDocument:
        raise ParsingDeferred("ASR_PROVIDER_NOT_CONFIGURED", "audio requires a real ASR provider")
