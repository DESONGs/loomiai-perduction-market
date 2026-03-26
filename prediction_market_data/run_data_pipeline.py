import subprocess
import sys


def run_step(label: str, script: str) -> None:
    print(f"\n=== {label} ===")
    proc = subprocess.run([sys.executable, script], check=False)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def main() -> None:
    run_step("Download Polymarket Dataset", "download_data.py")
    run_step("Prepare Eval Markets", "prepare.py")
    print("\n数据下载与预处理完成。")


if __name__ == "__main__":
    main()
