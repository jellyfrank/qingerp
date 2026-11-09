def get_api_key_scope():
    return "qing_api"


def get_bearer_route_options(scope="qing_api"):
    return {
        "auth": "bearer",
        "bearer_scope": scope,
        "save_session": False,
    }
