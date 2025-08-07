# Start the local llama.cpp servers 
# This script is used to start the necessary servers for the AI IOX workflow.   
# It is typically run in the background to keep the servers running.
# It parses config.py to get the correct models for each server: embedding, ranking, and LLM.
from config import AIConfig, LLAMA_CPP_EXECUTABLE, LLAMA_CPP_EXECUTABLE_WITH_GPU

import shlex
import time, argparse


#now start the servers
import os
import subprocess
# Get the AI configuration
config = AIConfig()

if __name__ == "__main__":
    argparser = argparse.ArgumentParser(description="Start NuCore.AI LLM servers.")
    argparser.add_argument("--with_gpu", default=True, help="Use GPU for LLM server. Default is true.")
    argparser.add_argument("--with_reranker", default=False, help="Start a reranker server. Default is false.")
    argparser.add_argument("--with_embedding", default=False, help="Start an embedding server. Default is false.")


    args = argparser.parse_args()
    if args.with_gpu:
        _LLAMA_CPP_EXECUTABLE = LLAMA_CPP_EXECUTABLE_WITH_GPU
        # Use the GPU version of the LLM server executable if available
        print("Using GPU for LLM server.")
    else:
        _LLAMA_CPP_EXECUTABLE = LLAMA_CPP_EXECUTABLE
        print("Using CPU for LLM server.")

    # Start the LLM server in its own terminal
    # This server is used for generating responses based on the input queries.
    # It uses the Llama.cpp executable and the model specified in the config.
    if not os.path.exists(LLAMA_CPP_EXECUTABLE):
        raise FileNotFoundError(f"Llama.cpp executable not found: {config.LLAMA_CPP_EXECUTABLE}")           
    if not os.path.exists(os.path.join(config.__models_path__, config.__llm_model__)):
        raise FileNotFoundError(f"LLM model not found: {os.path.join(config.__models_path__, config.__llm_model__)}")



    # Prepare the arguments for the LLM server
    # This includes the model path, host, port, and any additional parameters.
    # The model path is constructed using the models directory from the config.
    # The host and port are also taken from the config.
    # The parameters include options for Jinja templating, model type, and temperature.
    # The server will run on the specified host and port, allowing it to handle requests.
    llm_server_args = config.__llm_model_server_args__
try:
    # Start the LLM server in a new terminal window
    # This allows the server to run independently and handle requests.
    # The server will output logs to the terminal for debugging purposes.
    llm_server_process = subprocess.Popen(
        [_LLAMA_CPP_EXECUTABLE] + shlex.split(llm_server_args), 
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    #stdout, stderr = llm_server_process.communicate()  # Wait for the server to start and capture output
    #print("Output:\n", stdout.decode())
    #if stderr:
    #    print("Errors:\n", stderr.decode())

except Exception as e:
    print(f"Failed to start LLM server: {e}")
    raise RuntimeError("Could not start LLM server. Please check the configuration and ensure the model exists.")

if args.with_reranker:
    # Ensure the reranker server is started only if the argument is provided
    print("Starting Reranker Server...")

    # Start the reranker server
    # This server is used to rerank documents based on their relevance to a query.
    # It uses the BGE reranker model specified in the config.
    if not os.path.exists(os.path.join(config.__models_path__, config.__reranker_model__)):
        raise FileNotFoundError(f"Reranker model not found: {os.path.join(config.__models_path__, config.__reranker_model__)}")
    reranker_server_args = config.__reranker_model_server_args__
    reranker_server_process = subprocess.Popen(
        [LLAMA_CPP_EXECUTABLE] + shlex.split(reranker_server_args), 
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

if args.with_embedding:
    # Ensure the embedding server is started only if the argument is provided
    print("Starting Embedding Server...")
    # Start the embedding server
    # This server is used to generate embeddings for documents.
    # It uses the Qwen3 embedding model specified in the config.
    if not os.path.exists(os.path.join(config.__models_path__, config.__embedding_model__)):
        raise FileNotFoundError(f"Embedding model not found: {os.path.join(config.__models_path__, config.__embedding_model__)}")
    embedding_server_args = config.__embedding_model_server_args__
    embedding_server_process = subprocess.Popen(
        [LLAMA_CPP_EXECUTABLE] + shlex.split(embedding_server_args), 
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )           

# Print a message indicating that the servers have been started successfully
print("NuCore.AI workflow servers started successfully.")
# The servers are now running and can be used for AI workflows involving retrieval-augmented generation.
# You can now interact with the LLM, reranker, and embedding servers as needed.     

# Note: This script assumes that the necessary models and configurations are in place. (see config.py)
# If any model is missing, it will raise a FileNotFoundError.
# Ensure that the paths to the models and executables are correct in the AIConfig class.
# The servers will run in separate terminal windows, allowing you to monitor their output and logs.
# You can stop the servers by closing the terminal windows or using appropriate commands in the terminal.       

# Now wait indefinitely to keep the servers running
try:
    while True:
        time.sleep(1000000)
        pass  # Keep the script running to keep the servers alive
except KeyboardInterrupt:
    print("Stopping servers...")
    llm_server_process.terminate()
    if args.with_reranker:
        reranker_server_process.terminate()
    if args.with_embedding:
        embedding_server_process.terminate()
    print("Servers stopped.")

