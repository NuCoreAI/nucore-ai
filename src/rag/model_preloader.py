"""
Background model preloader for LocalEmbedder.
Loads sentence-transformers in a background thread during app startup.
"""

import threading
from typing import Optional
DEFAULT_LOCAL_MODEL = 'all-MiniLM-L6-v2'

_preload_thread: Optional[threading.Thread] = None
_model_instance: Optional[object] = None
_model_ready = threading.Event()
_preload_error: Optional[Exception] = None
_no_model_specificed = False

def _load_model_background(model_name: str , device: str = None):
    """Background thread function to load the model."""
    global _model_instance, _preload_error
    try:
        if model_name.startswith("http://") or model_name.startswith("https://"):
            from .embedder import Embedder
            _model_instance = Embedder(embedder_url=model_name)
        else:
            from .local_embedder import LocalEmbedder
            _model_instance = LocalEmbedder(model_name=model_name, device=device)
        print(f"✓ Background model loading complete ({model_name})")
    except Exception as e:
        _preload_error = e
        print(f"✗ Background model loading failed: {e}")
    finally:
        _model_ready.set()


def start_preload(model_name: str, device: str = None):
    """
    Start loading the model in a background thread.
    Call this early in your application startup.
    
    :param model_name: The model to preload
    :param device: Device to load on ('cuda', 'cpu', or None)
    """
    global _preload_thread
    global _no_model_specificed
    if model_name is None:
        _no_model_specificed = True
        return # No model specified, do not start preload
    
    if _preload_thread is not None:
        return  # Already started
    
    print(f"Starting background model preload ({model_name})...")
    _preload_thread = threading.Thread(
        target=_load_model_background,
        args=(model_name, device),
        daemon=True,
        name="ModelPreloader"
    )
    _preload_thread.start()


def get_model(timeout: float = 30.0):
    """
    Get the preloaded model instance. 
    Blocks if model is still loading (up to timeout seconds).
    
    :param timeout: Max seconds to wait for model to load
    :return: LocalEmbedder instance
    :raises RuntimeError: If preload was never started or failed
    :raises TimeoutError: If model loading takes too long
    """
    if _no_model_specificed:
        return None
    
    if _preload_thread is None:
        raise RuntimeError("Model preload was never started. Call start_preload() first.")
    
    # Wait for loading to complete
    if not _model_ready.wait(timeout=timeout):
        raise TimeoutError(f"Model loading took longer than {timeout} seconds")
    
    if _preload_error is not None:
        raise RuntimeError(f"Model loading failed: {_preload_error}")
    
    return _model_instance


def is_ready() -> bool:
    """Check if model is loaded and ready."""
    return _model_ready.is_set() and _preload_error is None
