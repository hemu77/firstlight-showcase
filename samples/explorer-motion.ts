// FirstLight — SAI HEMANTH KILARU. Portfolio code sample; all rights reserved.
// Presentation motion, independent of physical Earth rotation and replay speed.
export const presentationRotationDegrees=(elapsedMs:number)=>0.5*elapsedMs/1000;
export function canRotate(s:{enabled:boolean;reduced:boolean;hidden:boolean;blocked:boolean;height:number;idleMs:number}){
  return s.enabled&&!s.reduced&&!s.hidden&&!s.blocked&&s.height>1500000&&s.idleMs>=10000;
}
export function smoothHeight(current:number,target:number,deltaMs:number,reduced:boolean){
  const bounded=Math.max(100,Math.min(30000000,target));
  return reduced?bounded:current+(bounded-current)*(1-Math.exp(-Math.min(deltaMs,64)/85));
}
