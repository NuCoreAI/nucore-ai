"""
Example usage of the Hebcal provider plugin system with runtime installation.
"""
import asyncio
from datetime import datetime


async def example_usage():
    """Demonstrate plugin-based holiday provider usage."""
    
    # Import the plugin loader
    from scheduler.plugin_loader import get_plugin_loader
    
    loader = get_plugin_loader()
    
    # List available providers
    print("Available providers:")
    for provider in loader.list_available_providers():
        print(f"  - {provider['name']}: {provider['description']}")
        print(f"    Package: {provider['package']}")
        print(f"    Loaded: {provider['loaded']}\n")
    
    # Get a provider instance (will auto-install if needed)
    print("Loading Hebcal provider...")
    hebcal = loader.get_provider_instance(
        'hebcal',
        tz_str="America/New_York",
        latitude=40.7128,
        longitude=-74.0060
    )
    
    if hebcal is None:
        print("Failed to load Hebcal provider")
        return
    
    # Fetch holidays for current year
    current_year = datetime.now().year
    print(f"\nFetching Jewish holidays for {current_year}...")
    holidays = await hebcal.holidays_for_year(current_year)
    
    # Show first 5 holidays
    print(f"\nFound {len(holidays)} holidays. First 5:")
    for event in holidays[:5]:
        print(f"\n{event.title}")
        print(f"  Date: {event.date}")
        if event.start:
            print(f"  Time: {event.start.strftime('%H:%M %Z')}")
        print(f"  Category: {event.category}")
        
        # Get context prompt for this holiday
        if hasattr(hebcal, 'get_holiday_context_prompt'):
            prompt = hebcal.get_holiday_context_prompt(event)
            print(f"\n  Prompt context (first 200 chars):")
            print(f"  {prompt[:200]}...")


async def example_resolve_event_with_context():
    """
    Example of how ResolveEvents would work with prompt context.
    
    User says: "Turn off all lights for Yom Kippur"
    """
    from scheduler.plugin_loader import get_plugin_loader
    
    loader = get_plugin_loader()
    hebcal = loader.get_provider_instance(
        'hebcal',
        tz_str="America/New_York",
        latitude=40.7128,
        longitude=-74.0060
    )
    
    if hebcal is None:
        return
    
    # Simulate ResolveEvents call
    event_name = "Yom Kippur"
    year = 2026
    
    holidays = await hebcal.holidays_for_year(year)
    
    # Find matching event
    matching_events = [
        h for h in holidays 
        if event_name.lower() in h.title.lower()
    ]
    
    if not matching_events:
        print(f"No events found matching '{event_name}'")
        return
    
    print(f"Found {len(matching_events)} matching events:\n")
    
    for event in matching_events:
        print(f"=== ResolveEvents Response ===")
        print(f"Event: {event.title}")
        print(f"Date: {event.date}")
        
        # This is the key part - return both data AND prompt context
        if hasattr(hebcal, 'get_holiday_context_prompt'):
            context = hebcal.get_holiday_context_prompt(event)
            print(f"\n{context}")
            print("\n" + "="*50)
            print("This context gets injected into the conversation,")
            print("allowing the AI to create proper automations with")
            print("correct timing (e.g., candle lighting -10 to -40 mins)")
            print("="*50 + "\n")


if __name__ == "__main__":
    print("Example 1: Basic usage")
    print("="*60)
    asyncio.run(example_usage())
    
    print("\n\n")
    print("Example 2: ResolveEvents with context")
    print("="*60)
    asyncio.run(example_resolve_event_with_context())
