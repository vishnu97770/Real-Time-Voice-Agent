// GLSL for the orb. Kept in strings, in one file, so scene.js reads as behaviour and this reads
// as look. Everything is driven by a handful of uniforms; nothing is computed on the CPU per
// vertex.

// Ashima/Stefan Gustavson simplex noise (public domain / MIT).
export const NOISE = /* glsl */ `
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0);
  const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy));
  vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz);
  vec3 l=1.0-g;
  vec3 i1=min(g.xyz,l.zxy);
  vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx;
  vec3 x2=x0-i2+C.yyy;
  vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857;
  vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.0*floor(p*ns.z*ns.z);
  vec4 x_=floor(j*ns.z);
  vec4 y_=floor(j-7.0*x_);
  vec4 x=x_*ns.x+ns.yyyy;
  vec4 y=y_*ns.x+ns.yyyy;
  vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy);
  vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0;
  vec4 s1=floor(b1)*2.0+1.0;
  vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;
  vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x);
  vec3 p1=vec3(a0.zw,h.y);
  vec3 p2=vec3(a1.xy,h.z);
  vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
  vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0);
  m=m*m;
  return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}
`;

// The core: a sphere whose surface breathes with noise, faster and deeper as the voice gets
// louder. Dark glass inside, a teal rim where it meets the light.
export const CORE_VERTEX = /* glsl */ `
${NOISE}
uniform float uTime;
uniform float uLevel;
uniform float uThink;
varying vec3 vNormal;
varying vec3 vView;
varying float vDisp;
varying vec3 vPos;

vec3 shape(vec3 p){
  float amp = 0.035 + uLevel * 0.15;
  float speed = 1.0 + uThink * 1.6;
  float n1 = snoise(p * 1.35 + vec3(0.0, uTime * 0.28 * speed, uTime * 0.12));
  float n2 = snoise(p * 2.9 - vec3(uTime * 0.5 * speed, 0.0, uTime * 0.2));
  return p * (1.0 + n1 * amp + n2 * amp * 0.4 * (0.3 + uLevel));
}

void main(){
  vec3 p = normalize(position);
  vec3 s = shape(p);

  // Normal from the shape itself (two nearby points on the surface), so the light follows
  // the wobble instead of the original sphere.
  vec3 t = normalize(cross(p, abs(p.y) > 0.99 ? vec3(1.0, 0.0, 0.0) : vec3(0.0, 1.0, 0.0)));
  vec3 b = normalize(cross(p, t));
  vec3 st = shape(normalize(p + t * 0.02));
  vec3 sb = shape(normalize(p + b * 0.02));
  vec3 n = normalize(cross(st - s, sb - s));
  if (dot(n, p) < 0.0) n = -n;

  vec4 mv = modelViewMatrix * vec4(s, 1.0);
  vNormal = normalize(normalMatrix * n);
  vView = -mv.xyz;
  vDisp = length(s) - 1.0;
  vPos = s;
  gl_Position = projectionMatrix * mv;
}
`;

export const CORE_FRAGMENT = /* glsl */ `
uniform float uTime;
uniform float uLevel;
uniform float uOpacity;
uniform vec3 uDeep;
uniform vec3 uMid;
uniform vec3 uAccent;
uniform vec3 uBright;
varying vec3 vNormal;
varying vec3 vView;
varying float vDisp;
varying vec3 vPos;

void main(){
  vec3 n = normalize(vNormal);
  vec3 v = normalize(vView);
  vec3 l = normalize(vec3(-0.45, 0.75, 0.65));

  float fres = pow(1.0 - clamp(dot(n, v), 0.0, 1.0), 2.3);
  float lam = clamp(dot(n, l) * 0.5 + 0.5, 0.0, 1.0);

  vec3 col = mix(uDeep, uMid, lam * 0.75 + vDisp * 1.6);
  col += uAccent * fres * (0.75 + uLevel * 1.5);
  col += uBright * pow(fres, 3.2) * 0.85;

  vec3 h = normalize(l + v);
  col += vec3(0.85, 1.0, 0.96) * pow(clamp(dot(n, h), 0.0, 1.0), 120.0) * 0.22;

  // faint contour lines: the surface reads as sound, not plastic
  float lines = smoothstep(0.93, 1.0, sin(vPos.y * 24.0 + vDisp * 9.0 - uTime * 1.1) * 0.5 + 0.5);
  col += uAccent * lines * 0.10 * (0.35 + uLevel);

  gl_FragColor = vec4(col * uOpacity, uOpacity);
}
`;

// A thin ring around the orb whose radius follows a travelling waveform.
export const RING_VERTEX = /* glsl */ `
attribute float aAngle;
uniform float uTime;
uniform float uLevel;
uniform float uRadius;
uniform float uPhase;
varying float vFade;

void main(){
  float a = aAngle;
  float w = sin(a * 3.0 + uTime * 1.3 + uPhase) * 0.5
          + sin(a * 7.0 - uTime * 1.9 + uPhase * 2.0) * 0.35
          + sin(a * 13.0 + uTime * 2.7) * 0.15;
  float r = uRadius + w * (0.02 + uLevel * 0.17);
  vec3 p = vec3(cos(a) * r, sin(a) * r, w * (0.01 + uLevel * 0.06));
  vFade = 0.55 + 0.45 * sin(a * 2.0 + uTime * 0.6 + uPhase);
  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
}
`;

export const RING_FRAGMENT = /* glsl */ `
uniform vec3 uColor;
uniform float uAlpha;
uniform float uOpacity;
varying float vFade;

void main(){
  float a = uAlpha * vFade * uOpacity;
  gl_FragColor = vec4(uColor * a, a);
}
`;

// The particle shell: dust in orbit that widens when the voice is loud and swirls when the
// agent is thinking.
export const DUST_VERTEX = /* glsl */ `
attribute vec4 aSeed;
uniform float uTime;
uniform float uLevel;
uniform float uThink;
uniform float uPixel;
varying float vAlpha;

void main(){
  float r = 1.5 + aSeed.x * 1.9 + uLevel * 0.55 * aSeed.x;
  float speed = 0.03 + aSeed.w * 0.08;
  float th = aSeed.y + uTime * speed * (1.0 + uThink * 3.5);
  float ph = aSeed.z;
  vec3 p = vec3(r * sin(ph) * cos(th), r * cos(ph) * 0.78, r * sin(ph) * sin(th));
  p += normalize(p) * sin(uTime * 0.8 + aSeed.y * 6.0) * 0.05;

  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_Position = projectionMatrix * mv;
  gl_PointSize = (1.1 + aSeed.w * 2.6) * uPixel * (5.4 / -mv.z);
  vAlpha = (0.22 + 0.6 * aSeed.w) * (0.6 + 0.4 * sin(uTime * (0.6 + aSeed.w) + aSeed.y * 9.0));
}
`;

export const DUST_FRAGMENT = /* glsl */ `
uniform vec3 uColor;
uniform float uOpacity;
varying float vAlpha;

void main(){
  float d = length(gl_PointCoord - 0.5);
  float a = smoothstep(0.5, 0.0, d) * vAlpha * uOpacity;
  gl_FragColor = vec4(uColor * a, a);
}
`;
