// ----- Final representation of a trigger (Program) after parsing -----

import type { CmdParam, Val, ValWithUom, Var, VarType, VarWithUom } from './cmdTypes';

export type Trigger = {
  id: number;
  name: string;
  parent: number;
  isFolder?: boolean;
  if?: Condition[];
  then?: Action[];
  else?: Action[];
  comment?: string;
};

// Type used when uploading new programs.
// Same as Trigger, but it has no id, and the parent is optional
export type NewTrigger = Omit<Trigger, 'id' | 'parent'> & {
  parent?: number // We can take it, or we assign it the root folder.
}

// Type used when updating programs (parent is not required, it will inherit the existing one)
export type UpdatedTrigger = Omit<Trigger, 'parent'> & { parent?: number }

// When retrieving triggers, if there was a problem parsing, we get this format
export type InvalidTrigger = {
  id: number;
  name: string;
  parent: number;
  isFolder?: boolean;
  if?: Condition[];
  then?: Action[];
  else?: Action[];
  comment?: string;
  invalid: true;
  error: string;
  xml: string;
};

export type AndOr = 'and' | 'or'

export type StatusConditionOperator =
  | 'IS'
  | 'ISNOT'
  | 'GT' // >
  | 'LT' // <
  | 'GE' // >=
  | 'LE' // <=

export type VarConditionOperator = StatusConditionOperator // They are the same
export type OpenADRPriceOperator = StatusConditionOperator // They are the same

export type ControlConditionOperator = 'IS' | 'ISNOT'
export type OpenADRStatusModeOperator = ControlConditionOperator // They are the same

export type X10ConditionOperator =
  | 'IS'
  | 'ISNOT'
  | '=' // Same as IS? Probably obsolete.
// IS means "is received"  -- Triggers program if the message is received, condition set to True
// ISNOT means "is not received"  -- Triggers program if the message is received, condition set to False

// ---- Conditions ----

export type Condition =
  | Paren
  | Schedule
  | Control
  | Status
  | VarCondition
  | TriggerRef
  | X10Condition
  | InetOpenADR
  | CommentCondition;

type Paren = {
  type: 'paren';
  andOr: AndOr;
  conditions: Condition[];
}

// ---------- Schedule ----------

export type Schedule = {
  type: 'schedule';
  andOr: AndOr;
  daysofweek?: ScheduleDayOfWeek; // If missing, means all days
} & (ScheduleFromTo | ScheduleFromFor | ScheduleAt);

// --- These are the 3 types of schedules ---
type ScheduleFromTo = {
  from: ScheduleFrom,
  to: ScheduleTo;
}

type ScheduleFromFor = {
  from: ScheduleFrom,
  for: ScheduleFor;
}

type ScheduleAt = {
  at: ScheduleFrom, // at: It's really the same structure as From
}

// --- Schedule: Definition for From / To / For ---
export type ScheduleFrom = ScheduleTypeSunriseSunsetFrom | ScheduleTypeLastRunFrom | ScheduleTypeTimeFrom

export type ScheduleTo = ScheduleFrom & {
  offsetDays?: number // Must be string in format YYYY/MM/DD. If missing, it means daily.
}

export type ScheduleFor = {
  type: 'for',
  hours?: number,
  minutes?: number,
  seconds?: number
}

// This is the 'type' fields we can have in a schedule: 'from' | 'to' | 'at' | 'for'
export type ScheduleType = ScheduleFrom['type'] | 'for'


// Within from/to/at, we can have: lastruntime/sunset/sunrise/time

// Note on the daily flag:
// If daily:
//    If sunrise/sunset/time:
//        There is NO date field.
//        There is an offsetDays if sunrise/sunset/time is within a "to" (not from)
//    If lastruntime:
//        There is a daily flag
//        **There may be an offsetDays if "lastruntime" is within a "to" (not from) - Probably an AC bug
// if NOT daily:
//    If sunrise/sunset/time:
//        There is a date field.
//        **There may be an offsetDays if sunrise/sunset/time is within a "to" (not from)
//              I think this may come from an AC bug. It makes no sense to have a date and an offsetDays (day tag in from/to/at)
//    If lastruntime
//        There is NO daily flag
//        **There may be an offsetDays if "lastruntime" is within a "to" (not from) - Probably an AC bug

// Note on offsetDays
// When not daily, the "to" can have an offsetDays set

export type ScheduleTypeLastRunFrom = {
  type: 'lastruntime',
  refid: number, // ProgramId
  offsetSec: number // Seconds after the last run (can't be negative)
  daily?: boolean // If true, it means daily.
}

export type ScheduleTypeSunriseSunsetFrom = {
  type: 'sunrise' | 'sunset',
  offsetSec: number // Can be positive or negative
  date?: string // Must be string in format YYYY/MM/DD. If missing, it means daily.
}

export type ScheduleTypeTimeFrom = {
  type: 'time',
  time: number, // seconds since midnight
  date?: string // Must be string in format YYYY/MM/DD. If missing, it means daily.
}


export type ScheduleDayOfWeek = {
  mon?: boolean;
  tue?: boolean;
  wed?: boolean;
  thu?: boolean;
  fri?: boolean;
  sat?: boolean;
  sun?: boolean;
};

// --- Enf of schedule related types ---

export type Control = {
  type: 'control';
  andOr: AndOr;
  id: string;
  node: string;
  op: ControlConditionOperator;
};

export type Status = {
  type: 'status';
  andOr: AndOr;
  id: string; // Property
  node: string;
  op: StatusConditionOperator;
  // We can compare to a val or var
  val?: ValWithUom;
  var?: VarWithUom;
};


type VarCondition = {
  type: 'var';
  andOr: AndOr;
  id: number;
  varType: VarType
  op: VarConditionOperator;
  // We can compare to a val or var
  val?: Val;
  var?: Var;
};

type TriggerRef = {
  type: 'triggerref';
  andOr: AndOr;
  refid: number;
  is: boolean;
};

type X10Condition = {
  type: 'x10';
  andOr: AndOr;
  hc: string,  // House code. Single letter from A-P
  uc?: number, // Unit code. The valid range should be 1-16 // If missing, it must be invalid
  cc: number   // Command code. The valid range should be 0-15
  op: X10ConditionOperator;
}

type CommentCondition = {
  type: 'comment';
  andOr: AndOr;
  comment: string;
};


type InetOpenADR = InetOpenADRPrice | InetOpenADRStatus | InetOpenADRControl;

type InetOpenADRPrice = {
  type: 'inet';
  andOr: AndOr;
  id: 'oadr'; // OpenADR is 1
  control: 'price'; // 1 is price
  op: OpenADRPriceOperator;
  action: number;
};

type InetOpenADRStatus = {
  type: 'inet';
  andOr: AndOr;
  id: 'oadr'; // OpenADR is 1
  control: 'status'; // 2 is status
  op: OpenADRStatusModeOperator;
  action: OpenADRActionStatus;
};

type InetOpenADRControl = {
  type: 'inet';
  andOr: AndOr;
  id: 'oadr'; // OpenADR is 1
  control: 'mode'; // 3 is mode
  op: OpenADRStatusModeOperator;
  action: OpenADRActionMode;
};

export type OpenADRControl = 'price' | 'status' | 'mode';
export type OpenADRActionStatus = 'inactive' | 'active' | 'pendingVeryNear' | 'pendingNear' | 'pendingFar' | 'pendingVeryFar'
export type OpenADRActionMode = 'none' | 'normal' | 'moderate' | 'high' | 'special'


// ---------- Common ---------------------



// ------------------------ Actions (Then / Else) -------------------

export type Action =
  | Cmd
  | VarAction
  | Notify
  | Wait
  | RunIf
  | RunThen
  | RunElse
  | Enable
  | Disable
  | Stop
  | Net
  | Repeat
  | RebootRun
  | RebootNotRun
  | Comment
  | AdjustScene
  | X10
  | System // Restart IoX / Electricity Demand Price Alert
  | Device



export type Cmd = {
  type: 'cmd';
  id: string; // cmdId
  node: string;
  p: CmdParam[];
}

// --- VarAction ---

export type VarActionOperator = 'EQ' | // =
  'ADD=' | // +=
  'SUB=' | // -=
  'MUL=' | // *=
  'DIV=' | // /=
  'REM=' | // %=
  'AND=' | // &=
  'OR=' |  // |=
  'XOR=' | // ^=
  'RDM=' | // Random
  'INIT'; // Init To

type VarActionCommon = {
  type: 'var';
  varType: VarType;
  id: number; // var id
  op: VarActionOperator;
};

type VarActionVal = VarActionCommon & {
  val: {
    value: number;
    prec?: number;
  }
};

type VarActionVar = VarActionCommon & {
  var: {
    type: VarType;
    id: number;
  }
};

type VarActionStatus = VarActionCommon & {
  status: {
    id: string; // Property
    node: string;
    uom: number;
  }
};

export const VarActionSysvalMap = {
  SecondsSinceStartOfDay: 1,
  MinutesSinceStartOfDay: 2,
  CurrentDayOfYear: 3,
  CurrentDayOfMonth: 4,
  CurrentDayOfWeek: 5,
  CurrentYear: 6,
  CurrentMonth: 7, // Jan=1
  CurrentHour: 8,
  CurrentMinute: 9,
  CurrentSecond: 10,
  SunriseToday: 11, // Seconds
  SunsetToday: 12, // Seconds
  SunriseTomorrow: 13, // Seconds
  SunsetTomorrow: 14, // Seconds
  UnixDateTime: 15,
} as const;

export type VarActionSysvalId = typeof VarActionSysvalMap[keyof typeof VarActionSysvalMap]

export type VarActionSysval = VarActionCommon & {
  sysval: {
    id: VarActionSysvalId;
  }
};

export type VarAction = VarActionVal | VarActionVar | VarActionStatus | VarActionSysval;

type Notify = {
  type: 'notify';
  content?: number;
  recipient: number;
};

type Wait = {
  type: 'wait';
  hours?: number;
  minutes?: number;
  seconds?: number;
  random?: boolean;
};

type RunIf = {
  type: 'runif';
  id: number;
};

type RunThen = {
  type: 'runthen';
  id: number;
};

type RunElse = {
  type: 'runelse';
  id: number;
};

type Enable = {
  type: 'enable';
  id: number;
};

type Disable = {
  type: 'disable';
  id: number;
};

type Stop = {
  type: 'stop';
  id: number;
};

type Net = {
  type: 'net';
  cmd: number; // 5 = Wol, 6 = Networking resource
  parm: number;
};

export type WhileConditionVar = {
  varType: VarType,
  id: number
}

export type WhileConditionVal = {
  value: number
  prec?: number
}

// While only supports var conditions
type WhileCondition = {
  var: {
    op: StatusConditionOperator
    varType: VarType
    id: number
    // We can compare to a val or var
    val?: WhileConditionVal
    var?: WhileConditionVar
  }
}


type Repeat = {
  type: 'repeat';
  for?: {
    times: number,
    random?: boolean
  };
  every?: {
    hours?: number;
    minutes?: number;
    seconds?: number;
  };
  while?: WhileCondition
};

type RebootRun = {
  type: 'rebootrun'; // Enable at startup
  id: number;
};

type RebootNotRun = {
  type: 'rebootnotrun'; // Disable at startup
  id: number;
};

type Comment = {
  type: 'comment';
  comment: string;
};

export type AdjustSceneType = 'cmd' | 'default' | 'ignore'

export type AdjustScene = {
  type: 'lp';
  group: string;
  ctlId: string; // Controller: Can be a node, or the scene itself
  rsp: {
    type: AdjustSceneType // Set to cmd, default or ignore
    node: string; // Which node to "Set" (one of the nodes in the group)
    cmd?: {
      cmdId: string;
      p?: CmdParam[];
    }
  }
};

type X10 = {
  type: 'x10';
  hc: string,  // House code. Single letter from A-P
  uc?: number, // Unit code. The valid range should be 1-16 // If missing, it must be invalid
  cc: number   // Command code. The valid range should be 0-15
}

export type SystemCommand = 1 | 17

type System  = {
  type: 'sys',
  cmd: SystemCommand
}

// This seems to be a query all
export type Device = {
  type: 'device';
  group: string, // this is normally the uuid
  control: string // This is normally ST
}
