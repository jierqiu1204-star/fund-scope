from fastapi import Request


async def allow_all(_: Request) -> None:
    return None
