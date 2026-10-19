import React from "react";
import { load } from "./utils";
import "./styles.css";
const lazy = import("./lazy");
const external = require("react");
export { load } from "./utils";

export function start() {
  return lazy && external && React && load;
}
