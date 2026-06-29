# Watson X Integration with Granite Models

This branch adds support for IBM Watson X AI with Granite models as an alternative to Ollama for running LocalGPT.

## Overview

LocalGPT now supports two LLM backends:
1. **Ollama** (default): Run models locally using Ollama
2. **Watson X**: Use IBM's Granite models hosted on Watson X AI

## What Changed

- Added `WatsonXClient` class in `rag_system/utils/watsonx_client.py` that provides an Ollama-compatible interface for Watson X
- Updated `factory.py` and `main.py` to support backend switching via environment variable
- Added `ibm-watsonx-ai` SDK dependency to `requirements.txt`
- Configuration now supports both backends through environment variables

## Prerequisites

To use Watson X with Granite models, you need:

1. IBM Cloud account with Watson X access
2. Watson X API key
3. Watson X project ID

### Getting Your Credentials

1. Go to [IBM Cloud](https://cloud.ibm.com/)
2. Navigate to Watson X AI service
3. Create or select a project
4. Get your API key from IBM Cloud IAM
5. Copy your project ID from the Watson X project settings

## Configuration

### Environment Variables

Create a `.env` file or set these environment variables:

```bash
# Choose LLM backend (default: ollama)
LLM_BACKEND=watsonx

# Watson X Configuration
WATSONX_API_KEY=your_api_key_here
WATSONX_PROJECT_ID=your_project_id_here
WATSONX_URL=https://us-south.ml.cloud.ibm.com

# Model Configuration
WATSONX_GENERATION_MODEL=ibm/granite-13b-chat-v2
WATSONX_ENRICHMENT_MODEL=ibm/granite-8b-japanese
```

### Available Granite Models

Watson X offers several Granite models:
- `ibm/granite-13b-chat-v2` - General purpose chat model
- `ibm/granite-13b-instruct-v2` - Instruction-following model
- `ibm/granite-20b-multilingual` - Multilingual support
- `ibm/granite-8b-japanese` - Lightweight Japanese model
- `ibm/granite-3b-code-instruct` - Code generation model

For a full list of available models, visit the [Watson X documentation](https://www.ibm.com/docs/en/watsonx/saas?topic=solutions-supported-foundation-models).

## Installation

1. Install the Watson X SDK:
```bash
pip install ibm-watsonx-ai>=1.3.39
```

Or install all dependencies:
```bash
pip install -r rag_system/requirements.txt
```

## Usage

### Running with Watson X

Once configured, simply set the environment variable and run as normal:

```bash
export LLM_BACKEND=watsonx
python -m rag_system.main api
```

Or in Python:

```python
import os
os.environ['LLM_BACKEND'] = 'watsonx'

from rag_system.factory import get_agent

# Get agent with Watson X backend
agent = get_agent(mode="default")

# Use as normal
result = agent.run("What is artificial intelligence?")
print(result)
```

### Switching Between Backends

You can easily switch between Ollama and Watson X:

```bash
# Use Ollama (local)
export LLM_BACKEND=ollama
python -m rag_system.main api

# Use Watson X (cloud)
export LLM_BACKEND=watsonx
python -m rag_system.main api
```

## Features

The Watson X client supports all the key features used by LocalGPT:

- ✅ Text generation / completion
- ✅ Async generation
- ✅ Streaming responses
- ✅ Embeddings (if using Watson X embedding models)
- ✅ Custom generation parameters (temperature, max_tokens, top_p, top_k)
- ⚠️ Image/multimodal support (limited, depends on model availability)

## API Compatibility

The `WatsonXClient` provides the same interface as `OllamaClient`:

```python
from rag_system.utils.watsonx_client import WatsonXClient

client = WatsonXClient(
    api_key="your_api_key",
    project_id="your_project_id"
)

# Generate completion
response = client.generate_completion(
    model="ibm/granite-13b-chat-v2",
    prompt="Explain quantum computing"
)

print(response['response'])

# Stream completion
for chunk in client.stream_completion(
    model="ibm/granite-13b-chat-v2",
    prompt="Write a story about AI"
):
    print(chunk, end='', flush=True)
```

## Limitations

1. **Embedding Models**: Watson X uses different embedding models than Ollama. Make sure to configure embedding models appropriately in `main.py` if needed.

2. **Multimodal Support**: Image support varies by model availability in Watson X. Not all Granite models support multimodal inputs.

3. **Streaming**: Streaming support depends on the Watson X SDK version and may fall back to returning the full response at once.

4. **Rate Limits**: Watson X has API rate limits that may differ from local Ollama usage. Monitor your usage accordingly.

## Troubleshooting

### Authentication Errors

If you see authentication errors:
- Verify your API key is correct
- Check that your project ID matches an existing Watson X project
- Ensure your IBM Cloud account has Watson X access

### Model Not Found

If you get model not found errors:
- Verify the model ID is correct (e.g., `ibm/granite-13b-chat-v2`)
- Check that the model is available in your Watson X instance
- Some models may require additional permissions

### Connection Errors

If you experience connection issues:
- Check your internet connection
- Verify the Watson X URL is correct for your region
- Check IBM Cloud status page for service outages

## Cost Considerations

Unlike local Ollama, Watson X is a cloud service with usage-based pricing:
- Token-based pricing for generation
- Consider your query volume
- Monitor usage through IBM Cloud dashboard

## Reverting to Ollama

To switch back to local Ollama:

```bash
unset LLM_BACKEND  # or set LLM_BACKEND=ollama
python -m rag_system.main api
```

## Support

For Watson X specific issues:
- [IBM Watson X Documentation](https://www.ibm.com/docs/en/watsonx/saas)
- [Watson X Developer Hub](https://www.ibm.com/watsonx/developer/)
- [IBM Cloud Support](https://cloud.ibm.com/docs/get-support)

For LocalGPT issues:
- [LocalGPT GitHub Issues](https://github.com/PromtEngineer/localGPT/issues)

## Contributing

If you find issues with the Watson X integration or want to add features:
1. Create an issue describing the problem/feature
2. Submit a pull request with your changes
3. Ensure all tests pass

## License

This integration follows the same license as LocalGPT (MIT License).
