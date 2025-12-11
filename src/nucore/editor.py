from dataclasses import dataclass, field
from .uom import UOMEntry, supported_uoms


@dataclass
class EditorSubsetRange:
    """
    Defines a discrete set of allowed values for an editor range,
    using spans (e.g., '0-5') and individual values (e.g., '7,9').
    """

    uom: UOMEntry = field(metadata={"choices": supported_uoms})
    subset: str
    names: dict = field(default_factory=dict)

    def json(self):
        description = "Subset of allowed values" 
        out = {
            "uom": f"{self.uom.label} = {self.uom.description}",
            "description": description,
        }
        parts = []
        for s in self.subset.split(","):
            s = s.strip()
            if "-" in s:
                # It's a range 'a-b'
                out[f"{s}"]=[
                    { f'"{k}"' : v }
                      for k, v in self.names.items()
                ]
            else:
                # Single value
                val_name = self.names.get(s)
                out[f"{s}"]= val_name

        return out
    
    def get_description(self):
        """
        Returns a description of the subset.
        """
        #desc = f"Enum of Unit {self.uom.label}"
        desc = f"uom={self.uom.label} uom_id={self.uom.id}"
        return desc

    def get_names(self):
        """
        Returns a list of names for the subset.
        """
        names = []
        if not self.names:
            return names
        # Iterate through the subset and map names
        # to their corresponding values.
        # If a range is specified, it will be expanded.
        # Otherwise, it will return the individual values.
        # Example: "0-5, 7, 9" -> ["between 0-5", "7", "9"]
        # Example: "0-5, 7, 9" -> ["between 0-5", "7 [maps to 7]", "9 [maps to 9]"]
        for s in self.subset.split(","):
            s = s.strip()
            if "-" in s:
                # It's a range 'a-b'
                #names.append(f"between {s}")
                for k, v in self.names.items():
                    names.append(f"{k}:{v}")
            else:
                # Single value
                val_name = self.names.get(s)
                if val_name:
                    names.append(f"{s}:{val_name}")
                else:
                    names.append(s)
        return names

@dataclass
class EditorMinMaxRange:
    """
    Defines a continuous range with min, max, precision, and step attributes.
    """

    uom: UOMEntry = field(metadata={"choices": supported_uoms})
    min: float
    max: float
    prec: float = None
    step: float = None
    names: dict = field(default_factory=dict)

    def json(self):
        if self.uom.id == "25":
            label = "Discrete values"
        else:
            label = "Range"
        description = f"{label}: between {self.min} and {self.max} {self.uom}"
        out = {
            "uom": f"{self.uom.label} = {self.uom.description}",
            "description": description,
        }
        if self.step:
            out["steps"]=self.step
        if self.names:
            out["mappings"] = [
                {
                    k: v
                } for k, v in self.names.items()]
        return out 

    def get_description(self):
        """
        Returns a description of the range.
        """
        #desc = f"Range {self.min} to {self.max} Unit {self.uom.label} [uom id={self.uom.id}]"
        desc = f"Range: min={self.min},max={self.max},uom={self.uom.label},uom_id={self.uom.id}"
        if self.step:
            desc += f",step={self.step},precision={self.prec if self.prec else 1}"

        return desc
    
    def get_names(self):
        """
        Returns a dictionary of names for the range.
        """
        names = []
        if self.names:
            for k, v in self.names.items():
                names.append(f"{k}:{v}")
        return names

@dataclass
class Editor:
    """
    Definition of an editor, used to render a value or allow selection.
    It defines allowed values through one or more ranges.
    """

    id: str
    ranges: list[EditorSubsetRange | EditorMinMaxRange]

    def json(self):
        return {
            "id": self.id,
            "ranges": [r.json() for r in self.ranges]
        }
