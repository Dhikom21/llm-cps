# Running the CPS on k3s

Single-node k3s on the Raspberry Pi. Everything below is run **on the Pi**.

## 1. Install k3s

```bash
curl -sfL https://get.k3s.io | sh -
sudo k3s kubectl get nodes
```

Make `kubectl` usable without `sudo`:

```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $USER ~/.kube/config
echo 'export KUBECONFIG=~/.kube/config' >> ~/.bashrc
```

Label the node so the agent can be pinned to it:

```bash
kubectl label node cps-pi cps-role=edge
```

## 2. Build the two images

k3s uses containerd, not Docker, so images are imported rather than pulled.

```bash
sudo apt install -y docker.io
sudo usermod -aG docker $USER      # log out and back in

cd ~/llm-cps

# the agent image (Python + GPIO libraries + the lab_practice code)
docker build -f k8s/Dockerfile.agent -t cps/agent:local .

# BuildSim: wrap the arm64 binary you already built
printf 'FROM debian:bookworm-slim\nCOPY buildsim-pi /usr/local/bin/buildsim\nENTRYPOINT ["/usr/local/bin/buildsim"]\n' > /tmp/Dockerfile.buildsim
cp ~/buildsim-pi .
docker build -f /tmp/Dockerfile.buildsim -t cps/buildsim:local .

# hand both to k3s
docker save cps/agent:local    | sudo k3s ctr images import -
docker save cps/buildsim:local | sudo k3s ctr images import -
```

## 3. Deploy

```bash
kubectl apply -f k8s/10-namespace-and-config.yaml
kubectl apply -f k8s/20-buildsim.yaml
kubectl apply -f k8s/30-agent.yaml
kubectl apply -f k8s/40-evacuate.yaml

kubectl -n cps get pods -w
```

Viewer, from the laptop: **http://192.168.1.45:30090**

## 4. Everyday commands

```bash
kubectl -n cps logs -f deploy/agent
kubectl -n cps logs -f deploy/evacuate

# switch the agent from the rule baseline to the LLM version
kubectl -n cps set env deploy/agent LLM_BASE_URL=... LLM_MODEL=...
# (and edit the command: in 30-agent.yaml, then re-apply)

# run a fire
kubectl -n cps delete job inject-fire --ignore-not-found
kubectl -n cps apply -f k8s/40-evacuate.yaml

# the audit log survives pod restarts
sudo tail -f /var/lib/cps/audit/decisions.jsonl
```

## 5. Things that will bite

**The agent pod will not start twice.** gpiozero holds the pins exclusively, so
kill any `edge_agent.py` you are still running by hand before deploying —
otherwise the pod crash-loops with a GPIO-busy error and the cause is not
obvious from the logs.

**BuildSim forgets everything when its pod restarts.** State is in memory. The
agent re-registers on its next cycle, so the floor plan repopulates within
seconds, but a restart mid-demo will briefly empty the room.

**`Recreate`, not `RollingUpdate`.** The default strategy starts the new pod
before stopping the old one, which cannot work for a pod holding exclusive
hardware. Both the agent and BuildSim use `Recreate` for different reasons —
one for the pins, one for the in-memory state.

---

# Shipping the portable half to the university cluster

## What can go, and what cannot

| Component | Portable? | Why |
|---|---|---|
| BuildSim | yes | Plain HTTP service, no hardware |
| `evacuate.py` | yes | Reads and writes the twin only |
| `inject_fire.py` | yes | One REST call |
| LLM inference | yes | Better there — GPUs |
| **the agent** | **no** | GPIO, 1-Wire and I²C are physical to one board |

The agent stays on the Pi permanently. That is not a limitation to engineer
around; it is the defining property of a cyber-physical system. The cyber half
is elastic, the physical half has an address in a room.

## Three things to sort out first

**1. Architecture.** The Pi is `arm64`; a university cluster is almost
certainly `amd64`. Build multi-arch images:

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -t <registry>/cps-agent:0.1 --push .
```

Then set `imagePullPolicy: Always` and drop the `:local` tags — the `ctr images
import` route above only works on a machine you can log into.

**2. A registry.** `cps/agent:local` exists only inside your Pi's containerd. A
shared cluster needs a registry it can pull from, plus an `imagePullSecret` if
it is private. Ask whoever runs the cluster which registry they expect.

**3. Direction of traffic — the constraint that decides the design.**

Your Pi can reach the internet, but nothing can reach *in* to your Pi. A pod in
the university cluster therefore **cannot** call the agent, poll it, or push
commands to it.

So the edge must always initiate. If BuildSim moves to the cluster, the agent
keeps working unchanged, because it only ever makes outbound calls — it POSTs
its registration and PUTs its readings. Set `BUILDSIM_URL` in the ConfigMap to
the cluster's ingress hostname and nothing else changes.

What you cannot do is invert it: a cluster-side service that wants to command
the relay would have to dial in, and it cannot. If you need that later, the
agent has to poll for commands or hold an outbound WebSocket open — which is
how nearly every real IoT fleet works, and for exactly this reason.

**Use TLS and an ingress** if BuildSim is exposed off-campus. It has no
authentication of any kind — its own documentation says so — so anything that
can reach it can command every actuator it knows about.

## Suggested split

```
University cluster                 The Pi (k3s)
------------------                 ------------
BuildSim + ingress  <──────────── agent  (outbound only)
evacuate.py                        real hardware
LLM inference (GPU) <──────────── llm_edge_agent (outbound only)
Grafana / analysis
```

Both arrows point the same way. That single property is what makes the split
deployable at all, and it is worth a paragraph in the report: the network
topology of a cyber-physical system is not an implementation detail, it
determines which component is allowed to be in charge.
