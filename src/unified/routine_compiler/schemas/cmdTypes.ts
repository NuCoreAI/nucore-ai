/**
 * Base Command Param Types, used in both Programs and Groups
 * Used to share formatting in util functions
 * Reuse <val> type for Favorite Commands / Geofencing when implemented
 *
 * Group/Scene
 * <link cmd="CONFIG" node="ZY007_1" type="cmd">
 *     <p id="NUM">
 *         <var id="6" type="1" uom="107"/>
 *     </p>
 *     <p id="VAL">
 *         <val prec="0" uom="111">0</val>
 *     </p>
 * </link>
 *
 *
 * Program
 * <cmd id="CONFIG" node="ZY007_1">
 *     <p id="NUM">
 *         <val prec="0" uom="107">0</val>
 *     </p>
 *     <p id="VAL">
 *         <val prec="0" uom="111">0</val>
 *     </p>
 * </cmd>
 */


// ---------- Var / Val ---------------------

export type VarType = '1' | '2'

export type Val = {
  prec?: number;
  value: number;
};

export type Var = {
  type: VarType;
  id: number;
}

export type ValWithUom = Val & {
  uom: number;
};

export type VarWithUom = Var & {
  uom: number;
}


// --- CmdParam ---
export type CmdParamType = 'val' | 'var';

type CmdParamCommon = {
  type: CmdParamType
  id: string  // Comes from the id of the p tag
}

export type CmdParamVal = CmdParamCommon & {
  val: ValWithUom
};

export type CmdParamVar = CmdParamCommon & {
  var: VarWithUom
}

// Children of a cmd p tag can be either a value or a var
export type CmdParam = CmdParamVal | CmdParamVar;



