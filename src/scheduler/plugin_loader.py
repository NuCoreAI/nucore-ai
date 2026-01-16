"""
Plugin loader for holiday providers.
Dynamically installs and loads holiday provider modules from pip at runtime.
"""
from __future__ import annotations
import subprocess
import sys
import importlib
from typing import Dict, Type, Optional, List
from .aps_async_sun_scheduler import HolidayProvider


class ProviderPluginLoader:
    """Manages dynamic loading and installation of holiday provider plugins."""
    
    # Registry of known provider plugins
    KNOWN_PROVIDERS = {
        'hebcal': {
            'package': 'nucore-hebcal-provider',
            'module': 'nucore_hebcal_provider',
            'class': 'HebcalHolidayProvider',
            'description': 'Jewish holidays from Hebcal API'
        },
        'us_federal': {
            'package': 'nucore-us-federal-provider',
            'module': 'nucore_us_federal_provider',
            'class': 'USFederalHolidayProvider',
            'description': 'US Federal holidays'
        }
    }
    
    def __init__(self):
        self._loaded_providers: Dict[str, Type[HolidayProvider]] = {}
        self._installed_packages: set = set()
    
    def install_provider(self, provider_name: str) -> bool:
        """
        Install a provider plugin via pip at runtime.
        
        Args:
            provider_name: Name of the provider (e.g., 'hebcal', 'us_federal')
            
        Returns:
            True if installation successful, False otherwise
        """
        if provider_name not in self.KNOWN_PROVIDERS:
            print(f"Unknown provider: {provider_name}")
            print(f"Known providers: {', '.join(self.KNOWN_PROVIDERS.keys())}")
            return False
        
        provider_info = self.KNOWN_PROVIDERS[provider_name]
        package_name = provider_info['package']
        
        if package_name in self._installed_packages:
            print(f"Package {package_name} already installed")
            return True
        
        try:
            print(f"Installing {package_name}...")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", package_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE
            )
            self._installed_packages.add(package_name)
            print(f"Successfully installed {package_name}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"Failed to install {package_name}: {e}")
            return False
    
    def load_provider(self, provider_name: str, auto_install: bool = True) -> Optional[Type[HolidayProvider]]:
        """
        Load a provider plugin, optionally installing it first.
        
        Args:
            provider_name: Name of the provider (e.g., 'hebcal', 'us_federal')
            auto_install: If True, automatically install if not available
            
        Returns:
            Provider class if successful, None otherwise
        """
        if provider_name in self._loaded_providers:
            return self._loaded_providers[provider_name]
        
        if provider_name not in self.KNOWN_PROVIDERS:
            print(f"Unknown provider: {provider_name}")
            return None
        
        provider_info = self.KNOWN_PROVIDERS[provider_name]
        module_name = provider_info['module']
        class_name = provider_info['class']
        
        try:
            # Try to import the module
            module = importlib.import_module(module_name)
            provider_class = getattr(module, class_name)
            self._loaded_providers[provider_name] = provider_class
            print(f"Loaded provider: {provider_name}")
            return provider_class
        except (ImportError, AttributeError) as e:
            if auto_install:
                print(f"Provider {provider_name} not found, attempting to install...")
                if self.install_provider(provider_name):
                    # Try again after installation
                    try:
                        module = importlib.import_module(module_name)
                        provider_class = getattr(module, class_name)
                        self._loaded_providers[provider_name] = provider_class
                        print(f"Loaded provider: {provider_name}")
                        return provider_class
                    except (ImportError, AttributeError) as e2:
                        print(f"Failed to load provider after installation: {e2}")
                        return None
            else:
                print(f"Failed to load provider {provider_name}: {e}")
                return None
    
    def get_provider_instance(
        self,
        provider_name: str,
        **kwargs
    ) -> Optional[HolidayProvider]:
        """
        Get an instance of a provider with initialization parameters.
        
        Args:
            provider_name: Name of the provider
            **kwargs: Initialization parameters for the provider
            
        Returns:
            Provider instance if successful, None otherwise
        """
        provider_class = self.load_provider(provider_name)
        if provider_class is None:
            return None
        
        try:
            return provider_class(**kwargs)
        except Exception as e:
            print(f"Failed to instantiate provider {provider_name}: {e}")
            return None
    
    def list_available_providers(self) -> List[Dict[str, str]]:
        """List all available provider plugins."""
        return [
            {
                'name': name,
                'package': info['package'],
                'description': info['description'],
                'loaded': name in self._loaded_providers
            }
            for name, info in self.KNOWN_PROVIDERS.items()
        ]


# Global plugin loader instance
_plugin_loader = ProviderPluginLoader()


def get_plugin_loader() -> ProviderPluginLoader:
    """Get the global plugin loader instance."""
    return _plugin_loader
