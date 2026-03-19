from nucore import SharedEnumsBase

class IoXSharedEnums(SharedEnumsBase):

    def __init__(self, json_output: bool):
        super().__init__({
            "I_RR": None,
            "I_BL_KP": None,
            "I_NUM_255": None
        }, json_output=json_output)
    
