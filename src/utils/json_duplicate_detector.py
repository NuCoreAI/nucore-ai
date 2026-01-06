"""
JSON Duplicate Detector
Simple approach: find first {, find matching }, repeat
"""
import json
import time
from typing import List, Tuple, Dict, Any, Optional


class JSONDuplicateDetector:
    """Detects duplicate tool calls within a time window"""
    
    def __init__(self, time_window_seconds: int = 5):
        """
        Args:
            time_window_seconds: Time window in seconds to check for duplicates
        """
        self.time_window = time_window_seconds
        self.last_tool_json: Optional[str] = None
        self.last_tool_time: Optional[float] = None

    def get_valid_json_objects(self, text: str, debug_mode: bool) -> List[Dict[str, Any]]:
        """
        Extract valid JSON objects from text.
        
        Args:
            text: The input text containing JSON objects
        Returns:
            List of valid JSON objects
        """
        valid_jsons = JSONDuplicateDetector.remove_duplicates(text)
        if len(valid_jsons) == 0:
            return []
        # now go through the list and remove any objects that are identical to the last tool call within time window
        filtered_jsons = []
        for obj in valid_jsons:
            if not self.is_duplicate(obj, debug_mode):
                filtered_jsons.append(obj)
        return filtered_jsons
    
    def is_duplicate(self, tool_json: Dict[str, Any], debug_mode: bool) -> bool:
        """
        Check if tool_json is duplicate of last tool call within time window.
        
        Args:
            tool_json: The current tool JSON object to check
            debug_mode: Whether debug mode is enabled
        Returns:
            True if this is a duplicate within the time window, False otherwise
        """
        current_time = time.time()
        
        # Normalize JSON for comparison
        normalized = json.dumps(tool_json, sort_keys=True, separators=(',', ':'))
        
        # Check if we have a previous tool call
        if self.last_tool_json is None or self.last_tool_time is None:
            # First call, not a duplicate
            self.last_tool_json = normalized
            self.last_tool_time = current_time
            return False
        
        # Check if within time window
        time_diff = current_time - self.last_tool_time
        
        if time_diff <= self.time_window:
            # Within time window, check if same JSON
            if normalized == self.last_tool_json:
                # Duplicate! Don't update timestamp to keep checking against original
                if debug_mode:
                    print(f"Duplicate JSON tool call detected within {self.time_window} seconds.")  
                return True
        
        # Not a duplicate - update last seen
        self.last_tool_json = normalized
        self.last_tool_time = current_time
        return False
    
    def reset(self):
        """Reset the detector state"""
        self.last_tool_json = None
        self.last_tool_time = None

    @staticmethod
    def extract_json_objects(text: str) -> List[Tuple[str, int, int]]:
        """
        Extract JSON objects by finding first { then its matching }.
        Returns list of (json_string, start_pos, end_pos)
        """
        results = []
        i = 0
        
        while i < len(text):
            # Find first {
            if text[i] == '{':
                start = i
                depth = 0
                
                # Find matching }
                while i < len(text):
                    if text[i] == '{':
                        depth += 1
                    elif text[i] == '}':
                        depth -= 1
                        if depth == 0:
                            # Found the matching }
                            json_str = text[start:i+1]
                            results.append((json_str, start, i+1))
                            break
                    i += 1
            i += 1
        
        return results

    @staticmethod
    def find_duplicates(text: str) -> List[Tuple[str, int, int]]:
        """
        Find duplicate JSON objects. Returns list of duplicates and uniques json objects 
        """
        json_objects = JSONDuplicateDetector.extract_json_objects(text)
        seen = {}
        duplicates = []
        uniques = []
        
        for json_str, start, end in json_objects:
            try:
                obj = json.loads(json_str)
                # Normalize for comparison
                normalized = json.dumps(obj, sort_keys=True, separators=(',', ':'))
                
                if normalized in seen:
                    duplicates.append(obj)
                else:
                    seen[normalized] = True 
                    uniques.append(obj)
            except json.JSONDecodeError:
                pass
        
        return duplicates, uniques


    @staticmethod
    def remove_duplicates(text: str) -> str:
        """ returns only the unique json objects from the text """
        duplicates, uniques = JSONDuplicateDetector.find_duplicates(text)
        return uniques

