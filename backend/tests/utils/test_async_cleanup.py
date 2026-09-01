import anyio
import pytest

from app.utils.async_cleanup import close_async_resource


@pytest.mark.anyio
async def test_async_resource_cleanup_is_shielded_from_cancellation():
    class Resource:
        closed = False

        async def aclose(self):
            await anyio.sleep(0)
            self.closed = True

    resource = Resource()
    with anyio.CancelScope() as cancel_scope:
        cancel_scope.cancel()
        await close_async_resource(resource)

    assert resource.closed is True
