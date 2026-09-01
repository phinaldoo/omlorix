NO_STORE_HEADERS = {
    "Cache-Control": "no-store, private",
    "Pragma": "no-cache",
    "Expires": "0",
}


def apply_no_store_headers(response):
    for name, value in NO_STORE_HEADERS.items():
        response.headers[name] = value
    return response
