from typing import Any, AsyncIterator, Dict, Optional

from litellm import ChatCompletionRequest, ModelResponse

from codegate.providers.base import StreamGenerator
from codegate.providers.litellmshim import BaseAdapter, llamacpp_stream_generator


class LlamaCppAdapter(BaseAdapter):
    """
    This is just a wrapper around LiteLLM's adapter class interface that passes
    through the input and output as-is - LiteLLM's API expects OpenAI's API
    format.
    """

    def __init__(self, stream_generator: StreamGenerator = llamacpp_stream_generator):
        super().__init__(stream_generator)

    def translate_completion_input_params(self, kwargs: Dict) -> Optional[ChatCompletionRequest]:
        try:
            return ChatCompletionRequest(**kwargs)
        except Exception as e:
            raise ValueError(f"Invalid completion parameters: {str(e)}")

    def translate_completion_output_params(self, response: ModelResponse) -> Any:
        return response

    def translate_completion_output_params_streaming(
        self, completion_stream: AsyncIterator[ModelResponse]
    ) -> AsyncIterator[ModelResponse]:
        return completion_stream
