#configuration file for defaults
import os

DEFAULT_NUCORE_INSTALL_DIR = "workspace/nucore"

LLAMA_CPP_DIR = os.path.join(DEFAULT_NUCORE_INSTALL_DIR, "llama.cpp")
LLAMA_CPP_EXECUTABLE = os.path.join(os.path.expanduser("~"), LLAMA_CPP_DIR, "build.blis/bin/llama-server")
LLAMA_CPP_EXECUTABLE_WITH_GPU = os.path.join(os.path.expanduser("~"), LLAMA_CPP_DIR, "build.cuda/bin/llama-server")


class AIConfig:
    def __init__(self, install_dir:str=None, models_path:str=None):
        if not install_dir:
            install_dir = os.path.join(os.path.expanduser('~'), DEFAULT_NUCORE_INSTALL_DIR)
        self.__models_path__:str = models_path if models_path else os.path.join(install_dir, "models")

        self.__ragdb_file__ = "ragdb"

        self.__model_host__="localhost"
        self.__model_port__=8013
        self.__model_url__=f"http://{self.__model_host__}:{self.__model_port__}/v1/chat/completions"
        self.__llm_model__ = "finetuned/qwen2.5-coder-dls-7b/qwen2.5-coder-nucore-7b-Q4_K_M.gguf" 
        self.__llm_model_params__ = "-c 60000 --temp 0.0 --repeat-penalty 1.1 --n-gpu-layers 100 --batch-size 8196"
        self.__llm_model_server_args__ = f"-m {os.path.join(self.__models_path__,self.__llm_model__)} --host {self.__model_host__} --port {self.__model_port__} {self.__llm_model_params__}"

        self.__reranker_host__="localhost"
        self.__reranker_port__=8026
        self.__reranker_url__=f"http://{self.__reranker_host__}:{self.__reranker_port__}/v1/rerank"
        self.__reranker_model__ = "bge-reranker-v2-m3.gguf" 
        self.__reranker_model_params__ = "--reranking --temp 0.0 "
        self.__reranker_model_server_args__ = f"-m {os.path.join(self.__models_path__,self.__reranker_model__)} --host {self.__reranker_host__} --port {self.__reranker_port__} {self.__reranker_model_params__}"

        self.__embedding_host__="localhost"
        self.__embedding_port__=8052
        self.__embedding_url__=f"http://{self.__embedding_host__}:{self.__embedding_port__}/v1/embeddings"
        self.__embedding_model__ = "Qwen3-Embedding-0.6B-f16.gguf"
        self.__embedding_model_params__ = "--embeddings --pooling mean -ub 2048"
        self.__embedding_model_server_args__ = f"-m {os.path.join(self.__models_path__,self.__embedding_model__)} --host {self.__embedding_host__} --port {self.__embedding_port__} {self.__embedding_model_params__}"


    def getLLMModel(self, model:str=None):
        if not model:
            model = self.__llm_model__

        return os.path.join(self.__models_path__, model)

    def getRerankerModel(self, model:str=None):
        if not model:
            model = self.__llm_reranker_model__

        return os.path.join(self.__models_path__, model)
    
    def getEmbeddingModel(self, model:str=None):
        if not model:
            model = self.__llm_reranker_model__

        return os.path.join(self.__models_path__, model)

    def getModelURL(self):
        return self.__model_url__ 

    def getRerankerURL(self):
        return self.__reranker_url__    

    def getEmbeddingURL(self):
        return self.__embedding_url__

    
