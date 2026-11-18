"""Run OCR pytest against isolated settings, with optional branch coverage."""

import argparse
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT = Path(__file__).resolve().parents[4] / "OCR-document-parser"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", None}


def block_external_network(event, args):
    if event == "socket.connect" and isinstance(args[1], tuple):
        host = args[1][0]
    elif event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
        host = args[0]
    else:
        return
    if host not in LOCAL_HOSTS:
        raise RuntimeError("OCR tests block external networking. Mock the provider.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=PROJECT, help="Path to OCR-document-parser in the target checkout")
    parser.add_argument("--coverage-dir", type=Path, help="Write coverage data, an interactive report and a PNG infographic")
    parser.add_argument("--report-prefix", default="", help="Initial file-name prefix filter for the visual reports")
    parser.add_argument("selectors", nargs="*", help="Pytest paths or node IDs relative to OCR-document-parser")
    args = parser.parse_args()

    report_dir = args.coverage_dir.resolve() if args.coverage_dir else None
    if args.report_prefix and report_dir is None:
        parser.error("--report-prefix requires --coverage-dir")
    project = args.project_dir.resolve()
    test_dir = project / "tests"
    if not test_dir.is_dir():
        parser.error(f"Test directory not found: {test_dir}")
    selectors = []
    for selector in args.selectors or ["tests"]:
        path, separator, node = selector.partition("::")
        path = (project / path).resolve()
        if not path.is_relative_to(test_dir):
            parser.error("Select tests within OCR-document-parser/tests")
        selectors.append(f"{path}{separator}{node}")

    for name in tuple(os.environ):
        if name.startswith(("OCR_", "LANGFUSE_", "MLFLOW_", "OTEL_", "OPENAI_", "ROUTERAI_", "PYTEST_")):
            del os.environ[name]
    os.environ.update(
        OCR_DATABASE_URL="sqlite://",
        OCR_RABBITMQ_URL="amqp://guest:guest@127.0.0.1:1/%2F",
        OCR_LOG_FILE_PATH="",
        LANGFUSE_TRACING_ENABLED="false",
    )

    sys.path.insert(0, str(project / "src"))
    sys.addaudithook(block_external_network)
    import pytest

    original_dir = Path.cwd()
    with TemporaryDirectory(prefix="ocr-tests-") as temp:
        os.chdir(temp)
        os.environ["OCR_STORAGE_DIR"] = str(Path(temp) / "storage")
        try:
            pytest_args = [*selectors, "-q", "-o", f"cache_dir={Path(temp) / 'pytest-cache'}"]
            if report_dir is None:
                return pytest.main(pytest_args)

            from coverage import Coverage
            from report_coverage import write_reports

            test_stats = {}

            class TestSummary:
                def pytest_terminal_summary(self, terminalreporter):
                    test_stats.update({
                        name: len(terminalreporter.stats.get(name, ()))
                        for name in ("passed", "failed", "error", "skipped", "xfailed", "xpassed")
                    })

            coverage = Coverage(branch=True, source=["ocr_platform"], config_file=False, data_file=None)
            with coverage.collect():
                result = pytest.main(pytest_args, plugins=[TestSummary()])
            report_dir.mkdir(parents=True, exist_ok=True)
            has_coverage = bool(coverage.get_data().measured_files())
            if has_coverage:
                coverage.json_report(outfile=str(report_dir / "coverage.json"))
                coverage.html_report(directory=str(report_dir / "html"))
            write_reports(report_dir, project, test_stats, result,
                          args.selectors or ["tests"], args.report_prefix, has_coverage)
            print(f"Interactive report: {report_dir / 'dashboard.html'}")
            print(f"Infographic: {report_dir / 'infographic.png'}")
            return result
        finally:
            os.chdir(original_dir)


if __name__ == "__main__":
    raise SystemExit(main())
