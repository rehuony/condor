"""Response fixtures with the same streaming transport used by native clients."""

from contextlib import asynccontextmanager

from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.wrapper import CompletedStreamedResponse, WrapperModel


def response_model(respond):
    """Expose a fixed response's real parts and usage through stream events."""

    class ResponseStream(CompletedStreamedResponse):
        async def _get_event_iterator(self):
            for index, part in enumerate(self.response.parts):
                yield self._parts_manager.handle_part(vendor_part_id=index, part=part)

    class ResponseModel(WrapperModel):
        @asynccontextmanager
        async def request_stream(
            self, messages, model_settings, model_request_parameters, run_context=None
        ):
            response = await self.request(
                messages, model_settings, model_request_parameters
            )
            yield ResponseStream(model_request_parameters, response)

    return ResponseModel(FunctionModel(respond))
