from dotenv import load_dotenv

def get_agent(mode: str = "default"):
    """
    Factory function to get an instance of the RAG agent based on the specified mode.
    This uses local imports to prevent circular dependencies.
    """
    from rag_system.agent.loop import Agent
    from rag_system.utils.ollama_client import OllamaClient
    from rag_system.main import PIPELINE_CONFIGS, OLLAMA_CONFIG, LLM_BACKEND, WATSONX_CONFIG

    load_dotenv()
    
    # Initialize the appropriate LLM client based on backend configuration
    if LLM_BACKEND.lower() == "watsonx":
        from rag_system.utils.watsonx_client import WatsonXClient
        
        if not WATSONX_CONFIG["api_key"] or not WATSONX_CONFIG["project_id"]:
            raise ValueError(
                "Watson X configuration incomplete. Please set WATSONX_API_KEY and WATSONX_PROJECT_ID "
                "environment variables."
            )
        
        llm_client = WatsonXClient(
            api_key=WATSONX_CONFIG["api_key"],
            project_id=WATSONX_CONFIG["project_id"],
            url=WATSONX_CONFIG["url"]
        )
        llm_config = WATSONX_CONFIG
    else:
        llm_client = OllamaClient(host=OLLAMA_CONFIG["host"])
        llm_config = OLLAMA_CONFIG
    
    config = PIPELINE_CONFIGS.get(mode, PIPELINE_CONFIGS['default'])
    
    if 'storage' not in config:
        config['storage'] = {
            'db_path': 'lancedb',
            'text_table_name': 'text_pages_default',
            'image_table_name': 'image_pages'
        }
    
    agent = Agent(
        pipeline_configs=config, 
        llm_client=llm_client, 
        ollama_config=llm_config
    )
    return agent

def get_indexing_pipeline(mode: str = "default"):
    """
    Factory function to get an instance of the Indexing Pipeline.
    """
    from rag_system.pipelines.indexing_pipeline import IndexingPipeline
    from rag_system.main import PIPELINE_CONFIGS, OLLAMA_CONFIG, LLM_BACKEND, WATSONX_CONFIG
    from rag_system.utils.ollama_client import OllamaClient

    load_dotenv()
    
    # Initialize the appropriate LLM client based on backend configuration
    if LLM_BACKEND.lower() == "watsonx":
        from rag_system.utils.watsonx_client import WatsonXClient
        
        if not WATSONX_CONFIG["api_key"] or not WATSONX_CONFIG["project_id"]:
            raise ValueError(
                "Watson X configuration incomplete. Please set WATSONX_API_KEY and WATSONX_PROJECT_ID "
                "environment variables."
            )
        
        llm_client = WatsonXClient(
            api_key=WATSONX_CONFIG["api_key"],
            project_id=WATSONX_CONFIG["project_id"],
            url=WATSONX_CONFIG["url"]
        )
        llm_config = WATSONX_CONFIG
    else:
        llm_client = OllamaClient(host=OLLAMA_CONFIG["host"])
        llm_config = OLLAMA_CONFIG
    
    config = PIPELINE_CONFIGS.get(mode, PIPELINE_CONFIGS['default'])
    
    return IndexingPipeline(config, llm_client, llm_config)     