import {
  Component,
  Suspense,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ComponentRef,
  type ErrorInfo,
  type ReactNode,
} from "react";
import {
  Canvas,
  useFrame,
  useThree,
  type ThreeEvent,
} from "@react-three/fiber";
import { Html, Line, OrbitControls } from "@react-three/drei";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { gsap } from "gsap";
import {
  AlertTriangle,
  Atom,
  Check,
  ChevronDown,
  Crosshair,
  Eye,
  Focus,
  Info,
  LoaderCircle,
  Maximize2,
  Minus,
  MousePointer2,
  Pause,
  Play,
  Plus,
  RotateCcw,
  Ruler,
  Search,
  X,
} from "lucide-react";
import * as THREE from "three";
import type {
  MolecularAtom,
  MolecularBond,
  MolecularColorMode,
  MolecularRepresentation,
  MolecularScene,
} from "../types/molecular";
import "./molecule-viewer.css";

export type {
  MolecularAtom,
  MolecularBond,
  MolecularScene,
} from "../types/molecular";

export interface MoleculeViewerProps {
  scene: MolecularScene | null;
  loading?: boolean;
  onAtomSelect?: (atom: MolecularAtom | null) => void;
  className?: string;
}

const ELEMENT_COLORS: Record<string, string> = {
  C: "#a5c5a7",
  H: "#e5ebe4",
  N: "#78a9fa",
  O: "#f37f71",
  S: "#e8c65a",
  P: "#ecac61",
  F: "#90df9d",
  Cl: "#7fce7b",
  Br: "#b8876c",
  I: "#b99ae3",
  B: "#e5bba5",
  Fe: "#d49070",
  Zn: "#a7abc7",
  Mg: "#8cdebc",
  Ca: "#bad391",
  Na: "#b7a0df",
  K: "#c6a1e5",
};
const VDW_RADII: Record<string, number> = {
  H: 1.2,
  C: 1.7,
  N: 1.55,
  O: 1.52,
  F: 1.47,
  P: 1.8,
  S: 1.8,
  Cl: 1.75,
  Br: 1.85,
  I: 1.98,
  Fe: 1.8,
  Zn: 1.39,
  Mg: 1.73,
  Ca: 2.31,
};
const CHAIN_COLORS = [
  "#a8d4bb",
  "#90b9ea",
  "#e4b87c",
  "#c4a5e3",
  "#e29caa",
  "#9bd3d8",
];
const REPRESENTATIONS: Array<{
  value: MolecularRepresentation;
  label: string;
  title: string;
}> = [
  {
    value: "ballstick",
    label: "Ball & stick",
    title: "원자와 결합 차수를 함께 표시합니다",
  },
  { value: "stick", label: "Sticks", title: "결합 중심의 가는 막대 표현" },
  {
    value: "spacefill",
    label: "Spacefill",
    title: "반데르발스 반지름으로 표시합니다",
  },
  {
    value: "cartoon",
    label: "Backbone",
    title: "실제 Cα 좌표의 백본 궤적과 리간드를 표시합니다",
  },
];
const SOURCE_LABELS: Record<MolecularScene["source"], string> = {
  rdkit_conformer: "RDKit · 계산 배좌",
  alphafold3_prediction: "AlphaFold 3 · 예측 구조",
  experimental_pdb: "PDB · 실험 구조",
};
type CameraCommand = {
  serial: number;
  kind: "fit" | "focus" | "zoom" | "region";
  atom?: MolecularAtom;
  factor?: number;
  center?: THREE.Vector3;
  radius?: number;
};
type Scale = { width: number; angstrom: number; zoomPercent: number; distance: number };
type Segment = {
  start: THREE.Vector3;
  end: THREE.Vector3;
  color: string;
  radius: number;
};

function canonicalElement(element: string): string {
  return element[0]?.toUpperCase() + element.slice(1).toLowerCase();
}
function position(atom: MolecularAtom): THREE.Vector3 {
  return new THREE.Vector3(atom.x, atom.y, atom.z);
}
function confidenceColor(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "#7d8983";
  if (value >= 90) return "#6b9fea";
  if (value >= 70) return "#86cce3";
  if (value >= 50) return "#e9d471";
  return "#e89e66";
}
function atomColor(
  atom: MolecularAtom,
  mode: MolecularColorMode,
  chainIds: string[],
): string {
  if (mode === "confidence") return confidenceColor(atom.confidence);
  if (mode === "chain")
    return CHAIN_COLORS[
      Math.max(0, chainIds.indexOf(atom.chain_id ?? "")) % CHAIN_COLORS.length
    ];
  return ELEMENT_COLORS[canonicalElement(atom.element)] ?? "#c4a8ca";
}
function atomRadius(
  atom: MolecularAtom,
  representation: MolecularRepresentation,
): number {
  const radius = VDW_RADII[canonicalElement(atom.element)] ?? 1.7;
  if (representation === "spacefill") return radius;
  if (representation === "stick")
    return atom.element.toUpperCase() === "H" ? 0.1 : 0.16;
  return radius * (atom.element.toUpperCase() === "H" ? 0.2 : 0.265);
}
function atomLabel(atom: MolecularAtom): string {
  return `${atom.name || atom.element} · #${atom.index}${atom.residue_name ? ` · ${atom.residue_name} ${atom.residue_id ?? ""}` : ""}`;
}

/** Every cylinder is derived from an actual bond record. Aromatic bonds have a dashed second rail. */
function buildBondSegments(
  bonds: MolecularBond[],
  atomMap: Map<number, MolecularAtom>,
  mode: MolecularColorMode,
  chainIds: string[],
  representation: MolecularRepresentation,
): Segment[] {
  if (representation === "spacefill") return [];
  const segments: Segment[] = [];
  const radius = representation === "stick" ? 0.105 : 0.095;
  const append = (
    a: THREE.Vector3,
    b: THREE.Vector3,
    aColor: string,
    bColor: string,
    thickness = radius,
  ) => {
    const mid = a.clone().lerp(b, 0.5);
    segments.push({ start: a, end: mid, color: aColor, radius: thickness });
    segments.push({ start: mid, end: b, color: bColor, radius: thickness });
  };
  for (const bond of bonds) {
    const source = atomMap.get(bond.source);
    const target = atomMap.get(bond.target);
    if (!source || !target) continue;
    const a = position(source);
    const b = position(target);
    const direction = b.clone().sub(a);
    if (direction.lengthSq() < 0.000001) continue;
    direction.normalize();
    const axis =
      Math.abs(direction.dot(THREE.Object3D.DEFAULT_UP)) > 0.9
        ? new THREE.Vector3(1, 0, 0)
        : THREE.Object3D.DEFAULT_UP;
    const side = direction.clone().cross(axis).normalize().multiplyScalar(0.15);
    const aColor = atomColor(source, mode, chainIds);
    const bColor = atomColor(target, mode, chainIds);
    if (bond.aromatic || bond.order === 1.5) {
      append(
        a.clone().add(side),
        b.clone().add(side),
        aColor,
        bColor,
        radius * 0.75,
      );
      for (let dash = 0; dash < 4; dash++) {
        const start = a
          .clone()
          .lerp(b, (dash + 0.12) / 4)
          .sub(side);
        const end = a
          .clone()
          .lerp(b, (dash + 0.66) / 4)
          .sub(side);
        const color = dash < 2 ? aColor : bColor;
        append(start, end, color, color, radius * 0.6);
      }
    } else {
      const order = bond.order >= 3 ? 3 : bond.order >= 2 ? 2 : 1;
      for (let rail = 0; rail < order; rail++) {
        const shift = side
          .clone()
          .multiplyScalar(
            order === 3
              ? (rail - 1) * 1.45
              : order === 2
                ? rail === 0
                  ? -1
                  : 1
                : 0,
          );
        append(
          a.clone().add(shift),
          b.clone().add(shift),
          aColor,
          bColor,
          order > 1 ? radius * 0.72 : radius,
        );
      }
    }
  }
  return segments;
}

function AtomInstances({
  atoms,
  representation,
  mode,
  chainIds,
  onSelect,
  onFocus,
  onHover,
}: {
  atoms: MolecularAtom[];
  representation: MolecularRepresentation;
  mode: MolecularColorMode;
  chainIds: string[];
  onSelect: (atom: MolecularAtom) => void;
  onFocus: (atom: MolecularAtom) => void;
  onHover: (atom: MolecularAtom | null) => void;
}) {
  const meshRef = useRef<THREE.InstancedMesh>(null);
  const invalidate = useThree((state) => state.invalidate);
  useLayoutEffect(() => {
    const mesh = meshRef.current;
    if (!mesh) return;
    const transform = new THREE.Object3D();
    const color = new THREE.Color();
    for (let index = 0; index < atoms.length; index++) {
      const atom = atoms[index];
      transform.position.set(atom.x, atom.y, atom.z);
      transform.scale.setScalar(atomRadius(atom, representation));
      transform.updateMatrix();
      mesh.setMatrixAt(index, transform.matrix);
      mesh.setColorAt(index, color.set(atomColor(atom, mode, chainIds)));
    }
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    mesh.computeBoundingSphere();
    invalidate();
  }, [atoms, representation, mode, chainIds, invalidate]);
  const handle = (
    event: ThreeEvent<MouseEvent>,
    callback: (atom: MolecularAtom) => void,
  ) => {
    const atom = event.instanceId == null ? null : atoms[event.instanceId];
    if (atom) {
      event.stopPropagation();
      callback(atom);
    }
  };
  if (!atoms.length) return null;
  return (
    <instancedMesh
      ref={meshRef}
      args={[undefined, undefined, atoms.length]}
      onClick={(event) => handle(event, onSelect)}
      onDoubleClick={(event) => handle(event, onFocus)}
      onPointerMove={(event) => handle(event, onHover)}
      onPointerOut={() => onHover(null)}
    >
      <sphereGeometry
        args={[
          1,
          atoms.length > 10_000 ? 12 : 24,
          atoms.length > 10_000 ? 8 : 16,
        ]}
      />
      <meshStandardMaterial roughness={0.3} metalness={0.13} />
    </instancedMesh>
  );
}

function BondInstances({ segments }: { segments: Segment[] }) {
  const meshRef = useRef<THREE.InstancedMesh>(null);
  const invalidate = useThree((state) => state.invalidate);
  useLayoutEffect(() => {
    const mesh = meshRef.current;
    if (!mesh) return;
    const transform = new THREE.Object3D();
    const color = new THREE.Color();
    const up = new THREE.Vector3(0, 1, 0);
    for (let index = 0; index < segments.length; index++) {
      const segment = segments[index];
      const direction = segment.end.clone().sub(segment.start);
      transform.position.copy(segment.start).lerp(segment.end, 0.5);
      transform.quaternion.setFromUnitVectors(
        up,
        direction.clone().normalize(),
      );
      transform.scale.set(segment.radius, direction.length(), segment.radius);
      transform.updateMatrix();
      mesh.setMatrixAt(index, transform.matrix);
      mesh.setColorAt(index, color.set(segment.color));
    }
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    mesh.computeBoundingSphere();
    invalidate();
  }, [segments, invalidate]);
  if (!segments.length) return null;
  return (
    <instancedMesh
      ref={meshRef}
      args={[undefined, undefined, segments.length]}
      raycast={() => {}}
    >
      <cylinderGeometry args={[1, 1, 1, 10]} />
      <meshStandardMaterial roughness={0.45} metalness={0.08} />
    </instancedMesh>
  );
}

type BackbonePath = { atoms: MolecularAtom[]; points: THREE.Vector3[] };
function backbonePaths(atoms: MolecularAtom[]): BackbonePath[] {
  const chains = new Map<string, MolecularAtom[]>();
  for (const atom of atoms) {
    if (
      !atom.is_protein ||
      atom.name?.trim() !== "CA" ||
      atom.element.toUpperCase() !== "C"
    )
      continue;
    const key = atom.chain_id ?? "";
    if (!chains.has(key)) chains.set(key, []);
    chains.get(key)!.push(atom);
  }
  const paths: BackbonePath[] = [];
  for (const chainAtoms of chains.values()) {
    let segment: MolecularAtom[] = [];
    const flush = () => {
      if (segment.length >= 2)
        paths.push({ atoms: segment, points: segment.map(position) });
      segment = [];
    };
    for (const atom of chainAtoms) {
      const previous = segment.at(-1);
      if (previous) {
        const distance = position(previous).distanceTo(position(atom));
        const previousResidue = Number(
          previous.sequence_id ?? previous.residue_id,
        );
        const currentResidue = Number(atom.sequence_id ?? atom.residue_id);
        if (
          distance > 4.5 ||
          distance < 2 ||
          (Number.isFinite(previousResidue) &&
            Number.isFinite(currentResidue) &&
            currentResidue - previousResidue !== 1)
        )
          flush();
      }
      segment.push(atom);
    }
    flush();
  }
  return paths;
}

function Backbone({
  path,
  mode,
  chainIds,
  onSelect,
  onFocus,
}: {
  path: BackbonePath;
  mode: MolecularColorMode;
  chainIds: string[];
  onSelect: (atom: MolecularAtom) => void;
  onFocus: (atom: MolecularAtom) => void;
}) {
  const geometry = useMemo(() => {
    const curve = new THREE.CatmullRomCurve3(path.points, false, "centripetal");
    const segments = Math.max(8, (path.points.length - 1) * 5);
    const tube = new THREE.TubeGeometry(curve, segments, 0.29, 7, false);
    const colors = new Float32Array(tube.attributes.position.count * 3);
    const color = new THREE.Color();
    for (let ring = 0; ring <= segments; ring++) {
      const index = Math.min(
        path.atoms.length - 1,
        Math.round((ring / segments) * (path.atoms.length - 1)),
      );
      color.set(atomColor(path.atoms[index], mode, chainIds));
      for (let side = 0; side <= 7; side++)
        color.toArray(colors, (ring * 8 + side) * 3);
    }
    tube.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    return tube;
  }, [path, mode, chainIds]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  const nearest = (
    event: ThreeEvent<MouseEvent>,
    callback: (atom: MolecularAtom) => void,
  ) => {
    event.stopPropagation();
    let match = path.atoms[0];
    let min = Infinity;
    for (const atom of path.atoms) {
      const distance = position(atom).distanceToSquared(event.point);
      if (distance < min) {
        min = distance;
        match = atom;
      }
    }
    callback(match);
  };
  return (
    <mesh
      geometry={geometry}
      onClick={(event) => nearest(event, onSelect)}
      onDoubleClick={(event) => nearest(event, onFocus)}
    >
      <meshStandardMaterial vertexColors roughness={0.4} metalness={0.1} />
    </mesh>
  );
}

function CameraRig({
  center,
  radius,
  command,
  spinning,
  reducedMotion,
  onScale,
}: {
  center: THREE.Vector3;
  radius: number;
  command: CameraCommand;
  spinning: boolean;
  reducedMotion: boolean;
  onScale: (scale: Scale) => void;
}) {
  const controlsRef = useRef<ComponentRef<typeof OrbitControls>>(null);
  const { camera, invalidate, size } = useThree();
  const lastScale = useRef("");
  const tweenRef = useRef<gsap.core.Timeline | null>(null);
  const fitDistanceForCamera = (fitRadius = radius) => {
    if (!(camera instanceof THREE.PerspectiveCamera)) return 12;
    const halfFov = THREE.MathUtils.degToRad(camera.fov) / 2;
    return Math.max(5, fitRadius / Math.sin(Math.min(halfFov, Math.atan(Math.tan(halfFov) * camera.aspect))) * 1.18);
  };
  useEffect(() => {
    const controls = controlsRef.current;
    if (!controls || !(camera instanceof THREE.PerspectiveCamera)) return;
    tweenRef.current?.kill();
    const target =
      command.kind === "region" && command.center
        ? command.center.clone()
        : command.kind === "focus" && command.atom
        ? position(command.atom)
        : command.kind === "fit"
          ? center.clone()
          : controls.target.clone();
    const direction = camera.position.clone().sub(controls.target).normalize();
    if (direction.lengthSq() < 0.1 || command.kind === "fit")
      direction.set(0.4, 0.25, 1).normalize();
    const fitDistance = fitDistanceForCamera();
    const distance =
      command.kind === "region"
        ? fitDistanceForCamera(command.radius ?? radius)
        : command.kind === "fit"
        ? fitDistance
        : command.kind === "focus"
          ? 4.2
          : THREE.MathUtils.clamp(
              camera.position.distanceTo(controls.target) *
                (command.factor ?? 1),
              0.65,
              Math.max(100, radius * 25),
            );
    const destination = target.clone().add(direction.multiplyScalar(distance));
    camera.near = 0.02;
    camera.far = Math.max(1500, radius * 100);
    camera.updateProjectionMatrix();
    const timeline = gsap.timeline({
      onUpdate: () => {
        controls.update();
        invalidate();
      },
    });
    timeline.to(
      camera.position,
      {
        x: destination.x,
        y: destination.y,
        z: destination.z,
        duration: reducedMotion ? 0 : command.kind === "zoom" ? 0.3 : 0.7,
        ease: "power3.inOut",
      },
      0,
    );
    timeline.to(
      controls.target,
      {
        x: target.x,
        y: target.y,
        z: target.z,
        duration: reducedMotion ? 0 : command.kind === "zoom" ? 0.3 : 0.7,
        ease: "power3.inOut",
      },
      0,
    );
    tweenRef.current = timeline;
    invalidate();
    return () => {
      timeline.kill();
    };
  }, [camera, center, radius, command, reducedMotion, invalidate]);
  useFrame(() => {
    if (!controlsRef.current || !(camera instanceof THREE.PerspectiveCamera))
      return;
    const distance = camera.position.distanceTo(controlsRef.current.target);
    const pixelsPerAngstrom =
      size.height /
      (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * distance);
    if (!Number.isFinite(pixelsPerAngstrom) || pixelsPerAngstrom <= 0) return;
    const desired = 66 / pixelsPerAngstrom;
    const decade = Math.pow(10, Math.floor(Math.log10(desired)));
    const multiplier = desired / decade;
    const angstrom = (multiplier >= 5 ? 5 : multiplier >= 2 ? 2 : 1) * decade;
    const width = Math.round(angstrom * pixelsPerAngstrom);
    const zoomPercent = Math.round(fitDistanceForCamera() / distance * 100);
    const key = `${angstrom}:${width}:${zoomPercent}`;
    if (lastScale.current !== key) {
      lastScale.current = key;
      onScale({ width, angstrom, zoomPercent, distance });
    }
  });
  return (
    <OrbitControls
      ref={controlsRef}
      makeDefault
      enableDamping
      dampingFactor={0.12}
      enableZoom
      zoomSpeed={0.8}
      touches={{ ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_PAN }}
      minDistance={0.65}
      maxDistance={Math.max(100, radius * 25)}
      autoRotate={spinning && !reducedMotion}
      autoRotateSpeed={0.55}
      rotateSpeed={0.65}
      onStart={() => tweenRef.current?.kill()}
    />
  );
}

class ViewerErrorBoundary extends Component<
  { children: ReactNode },
  { error: boolean }
> {
  state = { error: false };
  static getDerivedStateFromError() {
    return { error: true };
  }
  componentDidCatch(_error: Error, _info: ErrorInfo) {
    /* The visible fallback preserves the scientific metadata. */
  }
  render() {
    return this.state.error ? (
      <div
        className="mv-state mv-webgl-error"
        role="alert"
        data-testid="webgl-fallback"
      >
        <AlertTriangle size={25} />
        <strong>3D 렌더러를 시작하지 못했습니다</strong>
        <span>
          브라우저의 하드웨어 가속과 WebGL 2 지원을 확인하세요. 원자 검색과 구조
          정보는 계속 사용할 수 있습니다.
        </span>
      </div>
    ) : (
      this.props.children
    );
  }
}

function EmptyViewer({ loading }: { loading: boolean }) {
  return (
    <div className="mv-state" role="status">
      {loading ? (
        <LoaderCircle className="mv-loading-icon" size={30} />
      ) : (
        <Atom size={34} strokeWidth={1.25} />
      )}
      <strong>
        {loading
          ? "분자 좌표를 불러오는 중"
          : "분자를 선택하면 구조가 나타납니다"}
      </strong>
      <span>
        {loading
          ? "원자 좌표와 결합 정보를 준비하고 있습니다."
          : "성분, 설계 후보 또는 AlphaFold 3 결과를 열어 원자 단위로 탐색하세요."}
      </span>
    </div>
  );
}

export function MoleculeViewer({
  scene,
  loading = false,
  onAtomSelect,
  className = "",
}: MoleculeViewerProps) {
  return (
    <div
      className={`molecular-viewer ${className}`}
      data-testid="molecule-viewer"
    >
      {scene?.atoms?.length ? (
        <ViewerSession
          key={`${scene.source}:${scene.metadata?.sha256 ?? scene.metadata?.smiles ?? ""}:${scene.label}:${scene.atoms.length}`}
          scene={scene}
          loading={loading}
          onAtomSelect={onAtomSelect}
        />
      ) : (
        <EmptyViewer loading={loading} />
      )}
    </div>
  );
}

function ViewerSession({
  scene,
  loading,
  onAtomSelect,
}: Required<Pick<MoleculeViewerProps, "loading">> & {
  scene: MolecularScene;
  onAtomSelect?: MoleculeViewerProps["onAtomSelect"];
}) {
  const hasProtein = scene.atoms.some((atom) => atom.is_protein);
  const [representation, setRepresentation] = useState<MolecularRepresentation>(
    hasProtein ? "cartoon" : "ballstick",
  );
  const [colorMode, setColorMode] = useState<MolecularColorMode>("element");
  const [showHydrogens, setShowHydrogens] = useState(false);
  const [selected, setSelected] = useState<MolecularAtom | null>(null);
  const [hovered, setHovered] = useState<MolecularAtom | null>(null);
  const [spinning, setSpinning] = useState(false);
  const [ruler, setRuler] = useState(false);
  const [measured, setMeasured] = useState<MolecularAtom[]>([]);
  const [showSettings, setShowSettings] = useState(false);
  const [showWarnings, setShowWarnings] = useState(false);
  const [query, setQuery] = useState("");
  const [searchError, setSearchError] = useState("");
  const [command, setCommand] = useState<CameraCommand>({
    serial: 0,
    kind: "fit",
  });
  const [scale, setScale] = useState<Scale>({ width: 50, angstrom: 1, zoomPercent: 100, distance: 0 });
  const reducedMotion = !!useReducedMotion();
  const hasConfidence =
    scene.source === "alphafold3_prediction" &&
    scene.atoms.some((atom) => atom.confidence != null);
  const chainIds = useMemo(
    () => [...new Set(scene.atoms.map((atom) => atom.chain_id ?? ""))],
    [scene.atoms],
  );
  const invalidCoordinates = useMemo(
    () =>
      scene.atoms.some(
        (atom) => ![atom.x, atom.y, atom.z].every(Number.isFinite),
      ) ||
      new Set(scene.atoms.map((atom) => atom.index)).size !==
        scene.atoms.length,
    [scene.atoms],
  );
  const bounds = useMemo(() => {
    const box = new THREE.Box3();
    for (const atom of scene.atoms)
      if ([atom.x, atom.y, atom.z].every(Number.isFinite))
        box.expandByPoint(position(atom));
    if (box.isEmpty()) return { center: new THREE.Vector3(), radius: 3 };
    const sphere = box.getBoundingSphere(new THREE.Sphere());
    return { center: sphere.center, radius: Math.max(1, sphere.radius) };
  }, [scene.atoms]);
  const selectedLigandAtoms = useMemo(() => {
    const requestedIds = scene.metadata?.selected_ligand_atom_ids;
    if (!Array.isArray(requestedIds)) return [];
    const ids = new Set(requestedIds.filter((id): id is number => Number.isInteger(id)));
    const matches = scene.atoms.filter((atom) => ids.has(atom.id) && !atom.is_protein);
    if (!matches.length) return [];
    // A complex can contain several copies. Fit one complete observed ligand,
    // not the envelope spanning multiple protein subunits or unrelated cofactors.
    const first = matches[0];
    return matches.filter((atom) => atom.chain_id === first.chain_id &&
      atom.residue_id === first.residue_id && atom.residue_name === first.residue_name);
  }, [scene.atoms, scene.metadata?.selected_ligand_atom_ids]);
  const selectedLigandBounds = useMemo(() => {
    if (!selectedLigandAtoms.length) return null;
    const box = new THREE.Box3();
    for (const atom of selectedLigandAtoms)
      if (atom.element.toUpperCase() !== "H" && [atom.x, atom.y, atom.z].every(Number.isFinite))
        box.expandByPoint(position(atom));
    if (box.isEmpty()) return null;
    const sphere = box.getBoundingSphere(new THREE.Sphere());
    return { center: sphere.center, radius: Math.max(2, sphere.radius + 1.5) };
  }, [selectedLigandAtoms]);
  const focusSelectedLigand = useCallback(() => {
    if (!selectedLigandBounds) return;
    setSpinning(false);
    setCommand((previous) => ({ serial: previous.serial + 1, kind: "region", ...selectedLigandBounds }));
  }, [selectedLigandBounds]);
  useEffect(() => {
    focusSelectedLigand();
  }, [focusSelectedLigand]);
  const visibleAtoms = useMemo(
    () =>
      scene.atoms.filter(
        (atom) =>
          (showHydrogens || atom.element.toUpperCase() !== "H") &&
          (representation !== "cartoon" || !atom.is_protein),
      ),
    [scene.atoms, showHydrogens, representation],
  );
  const visibleMap = useMemo(
    () => new Map(visibleAtoms.map((atom) => [atom.index, atom])),
    [visibleAtoms],
  );
  const segments = useMemo(
    () =>
      buildBondSegments(
        scene.bonds,
        visibleMap,
        colorMode,
        chainIds,
        representation,
      ),
    [scene.bonds, visibleMap, colorMode, chainIds, representation],
  );
  const paths = useMemo(
    () => (representation === "cartoon" ? backbonePaths(scene.atoms) : []),
    [scene.atoms, representation],
  );
  const atomMap = useMemo(
    () => new Map(scene.atoms.map((atom) => [atom.index, atom])),
    [scene.atoms],
  );
  const neighbors = useMemo(
    () =>
      selected
        ? scene.bonds.flatMap((bond) => {
            const index =
              bond.source === selected.index
                ? bond.target
                : bond.target === selected.index
                  ? bond.source
                  : null;
            const atom = index == null ? undefined : atomMap.get(index);
            return atom
              ? [
                  {
                    atom,
                    bond,
                    distance: position(selected).distanceTo(position(atom)),
                  },
                ]
              : [];
          })
        : [],
    [scene.bonds, atomMap, selected],
  );
  const warnings = useMemo(() => {
    const result = [...(scene.warnings ?? [])];
    if (scene.source === "rdkit_conformer")
      result.unshift(
        "계산된 단일 분자의 배좌입니다. 단백질 결합 자세나 결합력을 의미하지 않습니다.",
      );
    if (scene.source === "alphafold3_prediction")
      result.unshift(
        "AlphaFold 3의 구조 예측입니다. 결합 친화도·효능·안전성을 입증하지 않습니다.",
      );
    if (scene.source === "experimental_pdb")
      result.unshift(
        selectedLigandAtoms.length
          ? "선택 물질의 분자 구조가 일치하는 PDB 실험 좌표입니다. 실험에 사용한 단백질 구성과 조건은 출처에서 확인하세요."
          : "기존 PDB 실험 구조입니다. 현재 설계 후보의 결합 예측 결과와 구분해서 해석하세요.",
      );
    if (representation === "cartoon")
      result.push(
        "백본은 실제 Cα 좌표를 잇는 궤적입니다. 2차 구조를 별도로 할당한 리본 표현은 아닙니다.",
      );
    return [...new Set(result)];
  }, [scene.warnings, scene.source, representation, selectedLigandAtoms.length]);
  const act = useCallback(
    (kind: CameraCommand["kind"], atom?: MolecularAtom, factor?: number) => {
      setCommand((previous) => ({
        serial: previous.serial + 1,
        kind,
        atom,
        factor,
      }));
    },
    [],
  );
  const chooseAtom = useCallback(
    (atom: MolecularAtom) => {
      setSelected(atom);
      setSearchError("");
      onAtomSelect?.(atom);
      if (ruler)
        setMeasured((previous) =>
          previous.length === 1 && previous[0].index !== atom.index
            ? [previous[0], atom]
            : [atom],
        );
    },
    [onAtomSelect, ruler],
  );
  const focusAtom = useCallback(
    (atom: MolecularAtom) => {
      setSelected(atom);
      onAtomSelect?.(atom);
      setSpinning(false);
      act("focus", atom);
    },
    [act, onAtomSelect],
  );
  const search = (event: React.FormEvent) => {
    event.preventDefault();
    const value = query.trim().replace(/^#/, "");
    if (!value) return;
    const match = /^\d+$/.test(value)
      ? atomMap.get(Number(value))
      : scene.atoms.find(
          (atom) => atom.name?.toLowerCase() === value.toLowerCase(),
        );
    if (match) {
      if (match.element.toUpperCase() === "H") setShowHydrogens(true);
      if (
        representation === "cartoon" &&
        match.is_protein &&
        match.name !== "CA"
      )
        setRepresentation("ballstick");
      chooseAtom(match);
      focusAtom(match);
    } else setSearchError(`원자 “${query}”을 찾지 못했습니다.`);
  };
  const distance =
    measured.length === 2
      ? position(measured[0]).distanceTo(position(measured[1]))
      : null;
  const labelAtom = hovered ?? selected;
  const transition = { duration: reducedMotion ? 0 : 0.18 };
  const metadataSmiles =
    typeof scene.metadata?.smiles === "string" ? scene.metadata.smiles : null;
  const longBondCount =
    typeof scene.geometry?.long_bond_count === "number"
      ? scene.geometry.long_bond_count
      : 0;
  const shortBondCount =
    typeof scene.geometry?.short_bond_count === "number"
      ? scene.geometry.short_bond_count
      : 0;

  return (
    <>
      <div
        className="mv-canvas"
        data-testid="molecule-canvas"
        data-selected-ligand-atom-count={selectedLigandAtoms.length}
        data-structure-sha256={typeof scene.metadata?.sha256 === "string" ? scene.metadata.sha256 : undefined}
        tabIndex={0}
        role="group"
        aria-label="3D 분자 구조. 마우스 휠이나 두 손가락으로 확대·축소. 키보드 더하기·빼기로 줌, 숫자 0으로 전체 보기."
        aria-keyshortcuts="+ - 0"
        onPointerDown={(event) => event.currentTarget.focus({ preventScroll: true })}
        onKeyDown={(event) => {
          if (event.key === "+" || event.key === "=") {
            event.preventDefault(); act("zoom", undefined, 0.8);
          } else if (event.key === "-" || event.key === "_") {
            event.preventDefault(); act("zoom", undefined, 1.25);
          } else if (event.key === "0" || event.key === "Home") {
            event.preventDefault(); setSpinning(false); act("fit");
          }
        }}
      >
        {invalidCoordinates ? (
          <div className="mv-state" role="alert">
            <AlertTriangle />
            <strong>유효하지 않은 원자 좌표</strong>
            <span>
              중복 원자 번호 또는 유한하지 않은 좌표가 있어 구조를 표시할 수
              없습니다.
            </span>
          </div>
        ) : (
          <ViewerErrorBoundary>
            <Canvas
              camera={{
                position: [0, 0, Math.max(12, bounds.radius * 3)],
                fov: 38,
                near: 0.02,
                far: 3000,
              }}
              dpr={[1, 1.75]}
              frameloop={spinning && !reducedMotion ? "always" : "demand"}
              gl={{
                antialias: true,
                alpha: true,
                powerPreference: "high-performance",
              }}
              fallback={
                <div className="mv-state" role="alert">
                  <AlertTriangle />
                  <strong>WebGL 2를 사용할 수 없습니다</strong>
                  <span>하드웨어 가속을 지원하는 브라우저에서 열어주세요.</span>
                </div>
              }
              onPointerMissed={() => setHovered(null)}
            >
              <ambientLight intensity={1.2} />
              <directionalLight
                position={[10, 15, 20]}
                intensity={2.2}
                color="#fff5e9"
              />
              <directionalLight
                position={[-14, -4, -10]}
                intensity={1.3}
                color="#a1c9f5"
              />
              <Suspense fallback={null}>
                <AtomInstances
                  key={`atoms:${visibleAtoms.length}:${representation}`}
                  atoms={visibleAtoms}
                  representation={representation}
                  mode={colorMode}
                  chainIds={chainIds}
                  onSelect={chooseAtom}
                  onFocus={focusAtom}
                  onHover={setHovered}
                />
                <BondInstances
                  key={`bonds:${segments.length}`}
                  segments={segments}
                />
                {paths.map((path, index) => (
                  <Backbone
                    key={index}
                    path={path}
                    mode={colorMode}
                    chainIds={chainIds}
                    onSelect={chooseAtom}
                    onFocus={focusAtom}
                  />
                ))}
                {selected && (
                  <mesh
                    position={[selected.x, selected.y, selected.z]}
                    raycast={() => {}}
                  >
                    <sphereGeometry
                      args={[
                        atomRadius(selected, representation) * 1.18 + 0.08,
                        20,
                        12,
                      ]}
                    />
                    <meshBasicMaterial
                      color="#5ee7ee"
                      wireframe
                      transparent
                      opacity={0.65}
                      depthWrite={false}
                    />
                  </mesh>
                )}
                {labelAtom && (
                  <Html
                    position={[
                      labelAtom.x,
                      labelAtom.y + atomRadius(labelAtom, representation) + 0.3,
                      labelAtom.z,
                    ]}
                    center
                    zIndexRange={[5, 0]}
                    style={{ pointerEvents: "none" }}
                  >
                    <span className="mv-atom-label">
                      {atomLabel(labelAtom)}
                    </span>
                  </Html>
                )}
                {measured.length === 2 && (
                  <>
                    <Line
                      points={measured.map(
                        (atom) =>
                          [atom.x, atom.y, atom.z] as [number, number, number],
                      )}
                      color="#5ee7ee"
                      lineWidth={1.8}
                      dashed
                      dashSize={0.16}
                      gapSize={0.1}
                      depthTest={false}
                    />
                    <Html
                      position={position(measured[0]).lerp(
                        position(measured[1]),
                        0.5,
                      )}
                      center
                      zIndexRange={[6, 0]}
                      style={{ pointerEvents: "none" }}
                    >
                      <span className="mv-distance-label">
                        {distance?.toFixed(2)} Å
                      </span>
                    </Html>
                  </>
                )}
                <CameraRig
                  center={bounds.center}
                  radius={bounds.radius}
                  command={command}
                  spinning={spinning}
                  reducedMotion={reducedMotion}
                  onScale={setScale}
                />
              </Suspense>
            </Canvas>
          </ViewerErrorBoundary>
        )}
      </div>

      <div className="mv-topbar">
        <div className="mv-source">
          <span className="mv-source-dot" />
          <span>{SOURCE_LABELS[scene.source] ?? scene.source}</span>
        </div>
        <span className="mv-coordinate-badge">
          3D STRUCTURE <span>Å</span>
        </span>
      </div>
      <div className="mv-title-block">
        <span className="mv-eyebrow">MOLECULAR EXPLORER</span>
        <h3>{scene.label}</h3>
        <p>
          {scene.atoms.length.toLocaleString()} atoms <span>·</span>{" "}
          {scene.bonds.length.toLocaleString()} bonds
          {chainIds.some(Boolean) && (
            <>
              <span>·</span> {chainIds.length} chains
            </>
          )}
        </p>
      </div>

      <div
        className="mv-representations"
        role="group"
        aria-label="분자 표현 방식"
      >
        {REPRESENTATIONS.filter(
          (item) => item.value !== "cartoon" || hasProtein,
        ).map((item) => (
          <button
            key={item.value}
            data-testid={`representation-${item.value}`}
            aria-pressed={representation === item.value}
            title={item.title}
            className={representation === item.value ? "is-active" : ""}
            onClick={() => {
              setRepresentation(item.value);
              setHovered(null);
            }}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div className="mv-zoom-bar" role="group" aria-label="분자 확대 및 축소">
        <button
          title="축소 (−)"
          aria-label="축소"
          data-testid="viewer-zoom-out"
          onClick={() => act("zoom", undefined, 1.25)}
        >
          <Minus size={18} /><span>축소</span>
        </button>
        <output
          className="mv-zoom-level"
          data-testid="viewer-zoom-level"
          data-camera-distance={scale.distance.toFixed(4)}
          aria-label="전체 구조 맞춤 대비 확대 배율"
          title="전체 구조에 맞춘 거리를 100%로 표시합니다."
        >
          <strong>{scale.zoomPercent.toLocaleString()}<small>%</small></strong>
          <span>확대 배율</span>
        </output>
        <button
          title="확대 (+)"
          aria-label="확대"
          data-testid="viewer-zoom-in"
          onClick={() => act("zoom", undefined, 0.8)}
        >
          <Plus size={18} /><span>확대</span>
        </button>
        <span className="mv-zoom-divider" />
        <button
          title="전체 구조 맞춤 (0)"
          aria-label="전체 구조 맞춤"
          data-testid="viewer-reset"
          onClick={() => {
            setSpinning(false);
            act("fit");
          }}
        >
          <Maximize2 size={17} /><span>전체 보기</span>
        </button>
      </div>
      <div className="mv-tools" role="toolbar" aria-label="3D 구조 조작">
        {selectedLigandBounds && (
          <button
            title="선택한 물질의 실제 결합 위치에 초점"
            aria-label="선택 물질에 초점"
            data-testid="viewer-focus-ligand"
            onClick={focusSelectedLigand}
          >
            <Atom size={17} />
          </button>
        )}
        <button
          title="선택 원자로 이동"
          aria-label="선택 원자로 이동"
          disabled={!selected}
          data-testid="viewer-focus"
          onClick={() => selected && focusAtom(selected)}
        >
          <Focus size={17} />
        </button>
        <span className="mv-tool-divider" />
        <button
          title={
            spinning
              ? "회전 정지"
              : reducedMotion
                ? "동작 줄이기 설정으로 자동 회전이 꺼져 있습니다"
                : "자동 회전"
          }
          aria-label="자동 회전"
          aria-pressed={spinning}
          className={spinning ? "is-active" : ""}
          disabled={reducedMotion}
          data-testid="viewer-spin"
          onClick={() => setSpinning(!spinning)}
        >
          {spinning ? <Pause size={15} /> : <Play size={15} />}
        </button>
        <button
          title="두 원자 사이 거리 측정"
          aria-label="거리 측정"
          aria-pressed={ruler}
          data-testid="viewer-ruler"
          className={ruler ? "is-active" : ""}
          onClick={() => {
            setRuler(!ruler);
            setMeasured([]);
          }}
        >
          <Ruler size={17} />
        </button>
        <button
          title="표시 설정"
          aria-label="표시 설정"
          aria-expanded={showSettings}
          data-testid="viewer-settings"
          className={showSettings ? "is-active" : ""}
          onClick={() => setShowSettings(!showSettings)}
        >
          <Eye size={17} />
        </button>
      </div>

      <AnimatePresence>
        {showSettings && (
          <motion.div
            className="mv-settings mv-panel"
            initial={{ opacity: 0, x: 6 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 6 }}
            transition={transition}
          >
            <div className="mv-panel-heading">
              <strong>표시 설정</strong>
              <button
                aria-label="표시 설정 닫기"
                onClick={() => setShowSettings(false)}
              >
                <X size={14} />
              </button>
            </div>
            <label className="mv-field-label" htmlFor="molecular-color-mode">
              색상 기준
            </label>
            <select
              id="molecular-color-mode"
              data-testid="viewer-color-mode"
              value={colorMode}
              onChange={(event) =>
                setColorMode(event.target.value as MolecularColorMode)
              }
            >
              <option value="element">원소별 색상</option>
              <option value="chain">체인별 색상</option>
              <option value="confidence" disabled={!hasConfidence}>
                pLDDT 신뢰도{!hasConfidence ? " · 데이터 없음" : ""}
              </option>
            </select>
            <label className="mv-toggle">
              <input
                data-testid="viewer-hydrogens"
                type="checkbox"
                checked={showHydrogens}
                onChange={(event) => setShowHydrogens(event.target.checked)}
              />
              <span>수소 원자 표시</span>
              <span className="mv-toggle-track">
                <Check size={9} />
              </span>
            </label>
            <p className="mv-setting-note">
              {representation === "cartoon"
                ? "Cα 좌표의 백본 궤적. 원자 연결은 Ball & stick에서 확인하세요."
                : "원자 클릭: 정보 · 더블 클릭: 확대"}
              <br />
              휠 · 두 손가락: 줌 · 드래그: 회전 · 우클릭: 이동
            </p>
          </motion.div>
        )}
      </AnimatePresence>

      <form className="mv-search" onSubmit={search}>
        <Search size={13} />
        <input
          aria-label="원자 번호 또는 이름 검색"
          data-testid="atom-search"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setSearchError("");
          }}
          placeholder="원자 번호 / 이름"
        />
        <button
          type="submit"
          aria-label="원자 찾기"
          data-testid="atom-search-submit"
        >
          <Crosshair size={13} />
        </button>
      </form>
      {searchError && (
        <div className="mv-search-error" role="status">
          {searchError}
        </div>
      )}
      {ruler && (
        <div className="mv-ruler-instruction" role="status">
          <Ruler size={13} />
          {distance != null
            ? `#${measured[0].index} ↔ #${measured[1].index} · ${distance.toFixed(3)} Å`
            : `${measured.length ? "두 번째" : "첫 번째"} 원자를 선택하세요`}
          <button
            title="측정 초기화"
            aria-label="측정 초기화"
            onClick={() => setMeasured([])}
          >
            <RotateCcw size={12} />
          </button>
        </div>
      )}

      <AnimatePresence>
        {selected && (
          <motion.aside
            className="mv-atom-panel mv-panel"
            data-testid="atom-inspector"
            aria-label="선택 원자 정보"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8 }}
            transition={transition}
          >
            <div className="mv-panel-heading">
              <span className="mv-selected-title">
                <span
                  className="mv-element-disc"
                  style={{
                    color:
                      ELEMENT_COLORS[canonicalElement(selected.element)] ??
                      "#c4a8ca",
                  }}
                >
                  {selected.element}
                </span>
                <span>
                  <strong>{selected.name || selected.element}</strong>
                  <small>ATOM #{selected.index}</small>
                </span>
              </span>
              <button
                aria-label="원자 정보 닫기"
                onClick={() => {
                  setSelected(null);
                  onAtomSelect?.(null);
                }}
              >
                <X size={14} />
              </button>
            </div>
            <dl className="mv-atom-facts">
              <div>
                <dt>형식 전하</dt>
                <dd>
                  {selected.formal_charge == null
                    ? "—"
                    : selected.formal_charge > 0
                      ? `+${selected.formal_charge}`
                      : selected.formal_charge}
                </dd>
              </div>
              <div>
                <dt>결합 수</dt>
                <dd>{neighbors.length}</dd>
              </div>
              <div>
                <dt>체인 / 잔기</dt>
                <dd>
                  {selected.chain_id || "—"} /{" "}
                  {selected.residue_name
                    ? `${selected.residue_name} ${selected.residue_id ?? ""}`
                    : "—"}
                </dd>
              </div>
              <div>
                <dt>
                  {scene.source === "experimental_pdb" ? "B factor" : "pLDDT"}
                </dt>
                <dd>
                  {scene.source === "experimental_pdb"
                    ? (selected.b_factor?.toFixed(2) ?? "—")
                    : (selected.confidence?.toFixed(1) ?? "—")}
                </dd>
              </div>
            </dl>
            <div className="mv-coordinates">
              <span>X {selected.x.toFixed(3)}</span>
              <span>Y {selected.y.toFixed(3)}</span>
              <span>Z {selected.z.toFixed(3)}</span>
              <small>Å</small>
            </div>
            {neighbors.length > 0 && (
              <div className="mv-neighbors">
                <span className="mv-field-label">연결된 원자</span>
                <div>
                  {neighbors
                    .slice(0, 8)
                    .map(({ atom, bond, distance: bondDistance }) => (
                      <button
                        key={atom.index}
                        title={`원자 #${atom.index} · 결합 차수 ${bond.order}${bond.provenance ? ` · ${bond.provenance}` : ""}`}
                        onClick={() => chooseAtom(atom)}
                      >
                        <span
                          style={{
                            color:
                              ELEMENT_COLORS[canonicalElement(atom.element)],
                          }}
                        >
                          {atom.name || `${atom.element}${atom.index}`}
                        </span>
                        <span>
                          {bond.aromatic || bond.order === 1.5
                            ? "ar"
                            : bond.order === 2
                              ? "="
                              : bond.order === 3
                                ? "≡"
                                : "—"}
                        </span>
                        <small>{bondDistance.toFixed(2)} Å</small>
                      </button>
                    ))}
                </div>
              </div>
            )}
            <button
              className="mv-focus-button"
              onClick={() => {
                if (selected.is_protein) setRepresentation("ballstick");
                focusAtom(selected);
              }}
            >
              <Focus size={13} /> 원자 수준으로 확대
            </button>
          </motion.aside>
        )}
      </AnimatePresence>

      <div className="mv-bottom-left">
        <div className="mv-scale" title="현재 카메라 중심 평면의 길이 기준">
          <span style={{ width: Math.max(15, Math.min(100, scale.width)) }} />
          <small>{Number(scale.angstrom.toPrecision(3))} Å</small>
        </div>
        <div className="mv-legend">
          {colorMode === "confidence" ? (
            <>
              <span className="mv-confidence-strip" />
              <span>pLDDT 0–100</span>
              <span className="mv-muted">회색: 없음</span>
            </>
          ) : colorMode === "chain" ? (
            chainIds.slice(0, 5).map((chain, index) => (
              <span key={chain}>
                <i
                  style={{
                    backgroundColor: CHAIN_COLORS[index % CHAIN_COLORS.length],
                  }}
                />
                {chain || "리간드"}
              </span>
            ))
          ) : (
            ["C", "N", "O", "S", ...(showHydrogens ? ["H"] : [])]
              .filter((element) =>
                scene.atoms.some(
                  (atom) => atom.element.toUpperCase() === element,
                ),
              )
              .map((element) => (
                <span key={element}>
                  <i style={{ backgroundColor: ELEMENT_COLORS[element] }} />
                  {element}
                </span>
              ))
          )}
        </div>
        <div className="mv-interaction-hint">
          <MousePointer2 size={14} /> 휠로 확대·축소 <span>·</span> 드래그로 회전
          <span>·</span> 더블 클릭으로 원자 확대
        </div>
      </div>

      <div className="mv-provenance">
        <button
          data-testid="viewer-provenance"
          aria-expanded={showWarnings}
          onClick={() => setShowWarnings(!showWarnings)}
        >
          {longBondCount + shortBondCount > 0 ? (
            <AlertTriangle className="mv-quality-alert" size={12} />
          ) : (
            <Info size={12} />
          )}
          <span
            className={
              longBondCount + shortBondCount > 0
                ? "mv-quality-alert"
                : undefined
            }
          >
            {longBondCount + shortBondCount > 0
              ? `구조 품질 주의 · 비정상 결합 길이 ${(longBondCount + shortBondCount).toLocaleString()}개`
              : scene.source === "rdkit_conformer"
                ? "계산 배좌 · 결합 예측 전"
                : scene.source === "alphafold3_prediction"
                  ? "예측 구조 · 해석 시 주의"
                  : "실험 구조 · 참조 모델"}
          </span>
          <ChevronDown size={12} className={showWarnings ? "is-open" : ""} />
        </button>
        <AnimatePresence>
          {showWarnings && (
            <motion.div
              className="mv-provenance-details"
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 4 }}
              transition={transition}
            >
              {warnings.map((warning, index) => (
                <p key={index}>{warning}</p>
              ))}
              {scene.energy && (
                <p>
                  배좌 최적화: {scene.energy.method} ·{" "}
                  {scene.energy.converged ? "수렴" : "미수렴"}
                  {scene.energy.value_kcal_mol != null
                    ? ` · ${scene.energy.value_kcal_mol.toFixed(2)} kcal/mol`
                    : ""}
                </p>
              )}
              {metadataSmiles && (
                <details>
                  <summary>SMILES 확인</summary>
                  <code>{metadataSmiles}</code>
                </details>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      {loading && (
        <div className="mv-loading-badge" role="status">
          <LoaderCircle className="mv-loading-icon" size={12} />
          구조 업데이트 중
        </div>
      )}
    </>
  );
}

export default MoleculeViewer;
