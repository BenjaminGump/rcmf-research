from __future__ import annotations

"""Fallback AppWorld data downloader for SSL-intercepted networks.

Use the normal command first:

    appworld download data

Only use this script if the normal command fails with SSL verification errors.
It disables TLS certificate verification for the current Python process.
"""

import sys

import requests
import urllib3


def main() -> None:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    original_request = requests.Session.request

    def patched_request(self, method, url, *args, **kwargs):
        kwargs["verify"] = False
        return original_request(self, method, url, *args, **kwargs)

    requests.Session.request = patched_request
    from appworld.cli import app

    print("Running `appworld download data` with TLS verification disabled.")
    sys.argv = ["appworld", "download", "data"]
    try:
        app()
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise
    print("Finished AppWorld data download fallback.")


if __name__ == "__main__":
    main()

