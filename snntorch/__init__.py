import torch
import torch.nn as nn
import numpy as np


# device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
dtype = torch.float


class LIF(nn.Module):
    """Parent class for leaky integrate and fire neuron models."""

    instances = []
    """Each :mod:`snntorch.LIF` neuron (e.g., :mod:`snntorch.Synaptic`) will populate the :mod:`snntorch.LIF.instances` list with a new entry.
    The list is used to initialize and clear neuron states when the argument `init_hidden=True`."""

    def __init__(
        self,
        beta,
        threshold=1.0,
        spike_grad=None,
        inhibition=False,
        reset_mechanism="subtract",
    ):
        super(LIF, self).__init__()
        LIF.instances.append(self)

        self.beta = beta
        self.threshold = threshold
        self.inhibition = inhibition
        self.reset_mechanism = reset_mechanism

        if spike_grad is None:
            self.spike_grad = self.Heaviside.apply
        else:
            self.spike_grad = spike_grad

        if reset_mechanism != "subtract" and reset_mechanism != "zero":
            raise ValueError(
                "reset_mechanism must be set to either 'subtract' or 'zero'."
            )

    def fire(self, mem):
        """Generates spike if mem > threshold.
        Returns spk and reset."""
        mem_shift = mem - self.threshold
        spk = self.spike_grad(mem_shift)  # .to(device)
        reset = spk.clone().detach()

        return spk, reset

    def fire_inhibition(self, batch_size, mem):
        """Generates spike if mem > threshold, only for the largest membrane. All others neurons will be inhibited for that time step.
        Returns spk and reset."""
        mem_shift = mem - self.threshold
        index = torch.argmax(mem_shift, dim=1)
        spk_tmp = self.spike_grad(mem_shift)

        mask_spk1 = torch.zeros_like(spk_tmp)
        mask_spk1[torch.arange(batch_size), index] = 1
        spk = spk_tmp * mask_spk1  # .to(device)
        reset = spk.clone().detach()

        return spk, reset

    @classmethod
    def clear_instances(cls):
        """Removes all items from :mod:`snntorch.LIF.instances` when called."""
        cls.instances = []

    @staticmethod
    def init_leaky(*args):
        """
        Used to initialize mem.
        *args are the input feature dimensions (without time or batch size).
        E.g., Input feature of size=1x28x28 would require ``init_leaky(1, 28, 28)``.
        """
        mem = _SpikeTensor(*args, batch_flag=False)

        return mem

    @staticmethod
    def init_synaptic(*args, device="cpu"):
        """Used to initialize syn and mem.
        *args are the input feature dimensions (without time or batch size).
        E.g., Input feature of size=1x28x28 would require ``init_leaky(1, 28, 28)``.
        """
        syn = _SpikeTensor(*args, batch_flag=False, device_flag=device)
        mem = _SpikeTensor(*args, batch_flag=False, device_flag=device)

        return syn, mem

    @staticmethod
    def init_stein(*args):
        """Used to initialize syn and mem.
        *args are the input feature dimensions (without time or batch size).
        E.g., Input feature of size=1x28x28 would require ``init_stein(1, 28, 28)``.
        """

        return LIF.init_synaptic(*args)

    @staticmethod
    def init_lapicque(*args):
        """
        Used to initialize mem.
        *args are the input feature dimensions (without time or batch size).
        E.g., Input feature of size=1x28x28 would require ``init_lapicque(1, 28, 28)``.
        """

        return LIF.init_leaky(*args)

    @staticmethod
    def init_alpha(*args):
        """Used to initialize syn_exc, syn_inh and mem.
        *args are the input feature dimensions (without time or batch size).
        E.g., Input feature of size=1x28x28 would require ``init_alpha(1, 28, 28)``."""
        syn_exc = _SpikeTensor(*args, batch_flag=False)
        syn_inh = _SpikeTensor(*args, batch_flag=False)
        mem = _SpikeTensor(*args, batch_flag=False)

        return syn_exc, syn_inh, mem

    @staticmethod
    def detach(*args):
        """Used to detach input arguments from the current graph.
        Intended for use in truncated backpropagation through time where hidden state variables are global variables."""
        for state in args:
            state.detach_()

    @staticmethod
    def zeros(*args):
        """Used to clear hidden state variables to zero.
        Intended for use where hidden state variables are global variables."""
        for state in args:
            state = torch.zeros_like(state)

    @staticmethod
    class Heaviside(torch.autograd.Function):
        """Default spiking function for neuron.

        **Forward pass:** Heaviside step function shifted.

        .. math::

            S=\\begin{cases} 1 & \\text{if U ≥ U$_{\\rm thr}$} \\\\
            0 & \\text{if U < U$_{\\rm thr}$}
            \\end{cases}

        **Backward pass:** Heaviside step function shifted.

        .. math::

            \\frac{∂S}{∂U}=\\begin{cases} 1 & \\text{if U ≥ U$_{\\rm thr}$} \\\\
            0 & \\text{if U < U$_{\\rm thr}$}
            \\end{cases}

        Although the backward pass is clearly not the analytical solution of the forward pass, this assumption holds true on the basis that a reset necessarily occurs after a spike is generated when :math:`U ≥ U_{\\rm thr}`."""

        @staticmethod
        def forward(ctx, input_):
            out = (input_ > 0).float()
            ctx.save_for_backward(out)
            return out

        @staticmethod
        def backward(ctx, grad_output):
            (out,) = ctx.saved_tensors
            grad = grad_output * out
            return grad


class _SpikeTensor(torch.Tensor):
    """Set a batch_flag before hidden-state variables enter loop"""

    @staticmethod
    def __new__(cls, *args, batch_flag=False, device_flag="cpu", **kwargs):
        return super().__new__(cls, *args, **kwargs)

    def __init__(
        self,
        *args,
        batch_flag=True,
        device_flag="cpu",
    ):
        # super().__init__() # optional
        self.batch_flag = batch_flag
        self.device_flag = device_flag


def _SpikeTorchConv(*args, input_):
    """Convert SpikeTensor to torch.Tensor of zeros extended in the batch dimension.
    E.g., (1, 28, 28) of type _SpikeTensor--> (128, 1, 28, 28) of type torch.Tensor."""
    states = []
    if len(input_.size()) == 0:
        _batch_size = 1  # assume batch_size=1 if 1D input
    else:
        _batch_size = input_.size()[0]
    if len(args) == 1:  # if only one hidden state, make it iterable
        args = (args,)
    for arg in args:
        device = arg.device_flag
        arg = torch.Tensor(arg)
        arg = (
            torch.zeros_like(arg, dtype=dtype, device=device)
            .unsqueeze(0)
            .repeat(
                tuple([_batch_size] + torch.ones(len(arg.size()), dtype=int).tolist())
            )
        )
        states.append(arg)
    if len(states) == 1:  # otherwise, list isn't unpacked
        return states[0]

    return states


# # Neuron Models
class Leaky(LIF):
    """
    First-order leaky integrate-and-fire neuron model.
    Input is assumed to be a current injection.
    Membrane potential decays exponentially with rate beta.
    For :math:`U[T] > U_{\\rm thr} ⇒ S[T+1] = 1`.

    If `reset_mechanism = "subtract"`, then :math:`U[t+1]` will have `threshold` subtracted from it whenever the neuron emits a spike:

    .. math::

            U[t+1] = βU[t] + I_{\\rm in}[t+1] - RU_{\\rm thr}

    If `reset_mechanism = "zero"`, then :math:`U[t+1]` will be set to `0` whenever the neuron emits a spike:

    .. math::

            U[t+1] = βU[t] + I_{\\rm syn}[t+1] - R(βU[t] + I_{\\rm in}[t+1])

    * :math:`I_{\\rm in}` - Input current
    * :math:`U` - Membrane potential
    * :math:`U_{\\rm thr}` - Membrane threshold
    * :math:`R` - Reset mechanism: if active, :math:`R = 1`, otherwise :math:`R = 0`
    * :math:`β` - Membrane potential decay rate

    Example::

        import torch
        import torch.nn as nn
        import snntorch as snn

        alpha = 0.9
        beta = 0.5

        # Define Network
        class Net(nn.Module):
            def __init__(self):
                super().__init__()

                # initialize layers
                self.fc1 = nn.Linear(num_inputs, num_hidden)
                self.lif1 = snn.Leaky(beta=beta)
                self.fc2 = nn.Linear(num_hidden, num_outputs)
                self.lif2 = snn.Leaky(beta=beta)

            def forward(self, x, mem1, spk1, mem2):
                cur1 = self.fc1(x)
                spk1, mem1 = self.lif1(cur1, mem1)
                cur2 = self.fc2(spk1)
                spk2, mem2 = self.lif2(cur2, mem2)
                return mem1, spk1, mem2, spk2


    """

    def __init__(
        self,
        beta,
        threshold=1.0,
        num_inputs=False,
        spike_grad=None,
        batch_size=False,
        init_hidden=False,
        inhibition=False,
        reset_mechanism="subtract",
    ):
        super(Leaky, self).__init__(
            beta, threshold, spike_grad, inhibition, reset_mechanism
        )

        self.num_inputs = num_inputs
        self.batch_size = batch_size
        self.init_hidden = init_hidden

        if self.init_hidden:
            if not self.num_inputs:
                raise ValueError(
                    "num_inputs must be specified to initialize hidden states as instance variables."
                )
            elif not self.batch_size:
                raise ValueError(
                    "batch_size must be specified to initialize hidden states as instance variables."
                )
            elif hasattr(self.num_inputs, "__iter__"):
                self.spk, self.mem = self.init_leaky(
                    self.batch_size, *(self.num_inputs)
                )  # need to automatically call batch_size
            else:
                self.spk, self.mem = self.init_leaky(self.batch_size, self.num_inputs)
        if self.inhibition:
            if not self.batch_size:
                raise ValueError(
                    "batch_size must be specified to enable firing inhibition."
                )

    def forward(self, input_, mem):

        if hasattr(mem, "batch_flag"):  # only triggered on first-pass
            mem = _SpikeTorchConv(mem, input_=input_)

        if not self.init_hidden:
            if self.inhibition:
                spk, reset = self.fire_inhibition(self.batch_size, mem)
            else:
                spk, reset = self.fire(mem)

            if self.reset_mechanism == "subtract":
                mem = self.beta * mem + input_ - reset * self.threshold

            elif self.reset_mechanism == "zero":
                mem = self.beta * mem + input_ - reset * (self.beta * mem + input_)

            return spk, mem

        # intended for truncated-BPTT where instance variables are hidden states
        if self.init_hidden:
            if self.inhibition:
                self.spk, self.reset = self.fire_inhibition(self.batch_size, self.mem)
            else:
                self.spk, self.reset = self.fire(self.mem)

            if self.reset_mechanism == "subtract":
                self.mem = self.beta * self.mem + input_ - self.reset * self.threshold

            elif self.reset_mechanism == "zero":
                self.mem = (
                    self.beta * self.mem
                    + input_
                    - self.reset * (self.beta * self.mem + input_)
                )

            return self.spk, self.mem

    @classmethod
    def detach_hidden(cls):
        """Returns the hidden states, detached from the current graph.
        Intended for use in truncated backpropagation through time where hidden state variables are instance variables."""

        for layer in range(len(cls.instances)):
            if isinstance(cls.instances[layer], Leaky):
                cls.instances[layer].spk.detach_()
                cls.instances[layer].mem.detach_()

    @classmethod
    def zeros_hidden(cls):
        """Used to clear hidden state variables to zero.
        Intended for use where hidden state variables are instance variables."""

        for layer in range(len(cls.instances)):
            if isinstance(cls.instances[layer], Leaky):
                cls.instances[layer].spk = torch.zeros_like(cls.instances[layer].spk)
                cls.instances[layer].mem = torch.zeros_like(cls.instances[layer].mem)


class Synaptic(LIF):
    """
    2nd order leaky integrate and fire neuron model accounting for synaptic conductance.
    The synaptic current jumps upon spike arrival, which causes a jump in membrane potential.
    Synaptic current and membrane potential decay exponentially with rates of alpha and beta, respectively.
    For :math:`U[T] > U_{\\rm thr} ⇒ S[T+1] = 1`.

    If `reset_mechanism = "subtract"`, then :math:`U[t+1]` will have `threshold` subtracted from it whenever the neuron emits a spike:

    .. math::

            I_{\\rm syn}[t+1] = αI_{\\rm syn}[t] + I_{\\rm in}[t+1] \\\\
            U[t+1] = βU[t] + I_{\\rm syn}[t+1] - RU_{\\rm thr}

    If `reset_mechanism = "zero"`, then :math:`U[t+1]` will be set to `0` whenever the neuron emits a spike:

    .. math::

            I_{\\rm syn}[t+1] = αI_{\\rm syn}[t] + I_{\\rm in}[t+1] \\\\
            U[t+1] = βU[t] + I_{\\rm syn}[t+1] - R(βU[t] + I_{\\rm syn}[t+1])

    * :math:`I_{\\rm syn}` - Synaptic current
    * :math:`I_{\\rm in}` - Input current
    * :math:`U` - Membrane potential
    * :math:`U_{\\rm thr}` - Membrane threshold
    * :math:`R` - Reset mechanism: if active, :math:`R = 1`, otherwise :math:`R = 0`
    * :math:`α` - Synaptic current decay rate
    * :math:`β` - Membrane potential decay rate

    Example::

        import torch
        import torch.nn as nn
        import snntorch as snn

        alpha = 0.9
        beta = 0.5

        # Define Network
        class Net(nn.Module):
            def __init__(self):
                super().__init__()

                # initialize layers
                self.fc1 = nn.Linear(num_inputs, num_hidden)
                self.lif1 = snn.Synaptic(alpha=alpha, beta=beta)
                self.fc2 = nn.Linear(num_hidden, num_outputs)
                self.lif2 = snn.Synaptic(alpha=alpha, beta=beta)

            def forward(self, x, syn1, mem1, spk1, syn2, mem2):
                cur1 = self.fc1(x)
                spk1, syn1, mem1 = self.lif1(cur1, syn1, mem1)
                cur2 = self.fc2(spk1)
                spk2, syn2, mem2 = self.lif2(cur2, syn2, mem2)
                return syn1, mem1, spk1, syn2, mem2, spk2


    For further reading, see:

    *R. B. Stein (1965) A theoretical analysis of neuron variability. Biophys. J. 5, pp. 173-194.*

    *R. B. Stein (1967) Some models of neuronal variability. Biophys. J. 7. pp. 37-68.*"""

    def __init__(
        self,
        alpha,
        beta,
        threshold=1.0,
        num_inputs=False,
        spike_grad=None,
        batch_size=False,
        init_hidden=False,
        inhibition=False,
        reset_mechanism="subtract",
    ):
        super(Synaptic, self).__init__(
            beta, threshold, spike_grad, inhibition, reset_mechanism
        )

        self.alpha = alpha
        self.num_inputs = num_inputs
        self.batch_size = batch_size
        self.init_hidden = init_hidden

        if self.init_hidden:
            if not self.num_inputs:
                raise ValueError(
                    "num_inputs must be specified to initialize hidden states as instance variables."
                )
            elif not self.batch_size:
                raise ValueError(
                    "batch_size must be specified to initialize hidden states as instance variables."
                )
            elif hasattr(self.num_inputs, "__iter__"):
                self.spk, self.syn, self.mem = self.init_synaptic(
                    self.batch_size, *(self.num_inputs)
                )  # need to automatically call batch_size
            else:
                self.spk, self.syn, self.mem = self.init_synaptic(
                    self.batch_size, self.num_inputs
                )
        if self.inhibition:
            if not self.batch_size:
                raise ValueError(
                    "batch_size must be specified to enable firing inhibition."
                )

    def forward(self, input_, syn, mem):

        if hasattr(syn, "batch_flag") or hasattr(
            mem, "batch_flag"
        ):  # only triggered on first-pass
            syn, mem = _SpikeTorchConv(syn, mem, input_=input_)

        if not self.init_hidden:
            if self.inhibition:
                spk, reset = self.fire_inhibition(self.batch_size, mem)
            else:
                spk, reset = self.fire(mem)
            syn = self.alpha * syn + input_

            if self.reset_mechanism == "subtract":
                mem = self.beta * mem + syn - reset * self.threshold

            elif self.reset_mechanism == "zero":
                mem = self.beta * mem + syn - reset * (self.beta * mem + syn)

            return spk, syn, mem

        # intended for truncated-BPTT where instance variables are hidden states
        if self.init_hidden:
            if self.inhibition:
                self.spk, self.reset = self.fire_inhibition(self.batch_size, self.mem)
            else:
                self.spk, self.reset = self.fire(self.mem)
            self.syn = self.alpha * self.syn + input_

            if self.reset_mechanism == "subtract":
                self.mem = self.beta * self.mem + self.syn - self.reset * self.threshold

            elif self.reset_mechanism == "zero":
                self.mem = (
                    self.beta * self.mem
                    + self.syn
                    - self.reset * (self.beta * self.mem + self.syn)
                )

            return self.spk, self.syn, self.mem

    @classmethod
    def detach_hidden(cls):
        """Returns the hidden states, detached from the current graph.
        Intended for use in truncated backpropagation through time where hidden state variables are instance variables."""

        for layer in range(len(cls.instances)):
            if isinstance(cls.instances[layer], Synaptic):
                cls.instances[layer].spk.detach_()
                cls.instances[layer].syn.detach_()
                cls.instances[layer].mem.detach_()

    @classmethod
    def zeros_hidden(cls):
        """Used to clear hidden state variables to zero.
        Intended for use where hidden state variables are instance variables."""

        for layer in range(len(cls.instances)):
            if isinstance(cls.instances[layer], Synaptic):
                cls.instances[layer].spk = torch.zeros_like(cls.instances[layer].spk)
                cls.instances[layer].syn = torch.zeros_like(cls.instances[layer].syn)
                cls.instances[layer].mem = torch.zeros_like(cls.instances[layer].mem)


class Lapicque(LIF):
    """
    An extension of Lapicque's experimental comparison between extracellular nerve fibers and an RC circuit.
    It is qualitatively equivalent to :code:`Leaky` but defined using RC circuit parameters.
    Input stimulus is integrated by membrane potential which decays exponentially with a rate of beta.
    For :math:`U[T] > U_{\\rm thr} ⇒ S[T+1] = 1`.

    If `reset_mechanism = "subtract"`, then :math:`U[t+1]` will have `threshold` subtracted from it whenever the neuron emits a spike:

    .. math::

            U[t+1] = I_{\\rm in}[t+1] (\\frac{T}{C}) + (1- \\frac{T}{\\tau})U[t] - RU_{\\rm thr}

    If `reset_mechanism = "zero"`, then :math:`U[t+1]` will be set to `0` whenever the neuron emits a spike:

    .. math::

            U[t+1] = I_{\\rm in}[t+1] (\\frac{T}{\\tau}) + (1- \\frac{T}{\\tau})U[t] - R(I_{\\rm in}[t+1] (\\frac{T}{C}) + (1- \\frac{T}{\\tau})U[t])

    * :math:`I_{\\rm in}` - Input current
    * :math:`U` - Membrane potential
    * :math:`U_{\\rm thr}` - Membrane threshold
    * :math:`T`- duration of each time step
    * :math:`R` - Reset mechanism: if active, :math:`R = 1`, otherwise :math:`R = 0`
    * :math:`β` - Membrane potential decay rate

    Alternatively, the membrane potential decay rate β can be specified instead:

    .. math::

            β = e^{-1/RC}

    * :math:`β` - Membrane potential decay rate
    * :math:`R` - Parallel resistance of passive membrane (note: distinct from the reset $R$)
    * :math:`C` - Parallel capacitance of passive membrane

    * If only β is defined, then R will default to 1, and C will be inferred.
    * If RC is defined, β will be automatically calculated.
    * If (β and R) or (β and C) are defined, the missing variable will be automatically calculated.

    Example::

        import torch
        import torch.nn as nn
        import snntorch as snn

        beta = 0.5

        R = 1
        C = 1.44

        # Define Network
        class Net(nn.Module):
            def __init__(self):
                super().__init__()

                # initialize layers
                self.fc1 = nn.Linear(num_inputs, num_hidden)
                self.lif1 = snn.Lapicque(beta=beta)
                self.fc2 = nn.Linear(num_hidden, num_outputs)
                self.lif2 = snn.Lapicque(R=R, C=C)  # lif1 and lif2 are approximately equivalent

            def forward(self, x, mem1, spk1, mem2):
                cur1 = self.fc1(x)
                spk1, mem1 = self.lif1(cur1, mem1)
                cur2 = self.fc2(spk1)
                spk2, mem2 = self.lif2(cur2, mem2)
                return mem1, spk1, mem2, spk2


    For further reading, see:

    *L. Lapicque (1907) Recherches quantitatives sur l'excitation électrique des nerfs traitée comme une polarisation. J. Physiol. Pathol. Gen. 9, pp. 620-635. (French) *

    *N. Brunel and M. C. Van Rossum (2007) Lapicque's 1907 paper: From frogs to integrate-and-fire. Biol. Cybern. 97, pp. 337-339. (English)*

    Although Lapicque did not formally introduce this as an integrate-and-fire neuron model, we pay homage to his discovery of an RC circuit mimicking the dynamics of synaptic current."""

    def __init__(
        self,
        beta=False,
        R=False,
        C=False,
        time_step=1,
        threshold=1.0,
        num_inputs=False,
        spike_grad=None,
        batch_size=False,
        init_hidden=False,
        inhibition=False,
        reset_mechanism="subtract",
    ):
        super(Lapicque, self).__init__(
            beta, threshold, spike_grad, inhibition, reset_mechanism
        )

        self.beta = beta
        self.R = R
        self.C = C
        self.time_step = time_step
        self.num_inputs = num_inputs
        self.batch_size = batch_size
        self.init_hidden = init_hidden

        if not self.beta and not (self.R and self.C):
            raise ValueError(
                "Either beta or 2 of beta, R and C must be specified as an input argument."
            )

        elif not self.beta and (bool(self.R) ^ bool(self.C)):
            raise ValueError(
                "Either beta or 2 of beta, R and C must be specified as an input argument."
            )

        elif (self.R and self.C) and not self.beta:
            self.beta = torch.exp(torch.ones(1) * (-self.time_step / (self.R * self.C)))

        elif self.beta and not (self.R and self.C):
            self.R = 1
            self.C = self.time_step / (self.R * torch.log(torch.tensor(1 / self.beta)))

        elif self.beta and self.R and not self.C:
            self.C = self.time_step / (self.R * torch.log(torch.tensor(1 / self.beta)))

        elif self.beta and self.C and not self.R:
            self.R = self.time_step / (self.C * torch.log(torch.tensor(1 / self.beta)))

        if self.init_hidden:
            if not self.num_inputs:
                raise ValueError(
                    "num_inputs must be specified to initialize hidden states as instance variables."
                )
            elif not self.batch_size:
                raise ValueError(
                    "batch_size must be specified to initialize hidden states as instance variables."
                )
            elif hasattr(self.num_inputs, "__iter__"):
                self.spk, self.mem = self.init_lapicque(
                    self.batch_size, *(self.num_inputs)
                )  # need to automatically call batch_size
            else:
                self.spk, self.mem = self.init_lapicque(
                    self.batch_size, self.num_inputs
                )
        if self.inhibition:
            if not self.batch_size:
                raise ValueError(
                    "batch_size must be specified to enable firing inhibition."
                )

    def forward(self, input_, mem):

        if hasattr(mem, "batch_flag"):  # only triggered on first-pass
            mem = _SpikeTorchConv(mem, input_=input_)

        if not self.init_hidden:
            if self.inhibition:
                spk, reset = self.fire_inhibition(self.batch_size, mem)
            else:
                spk, reset = self.fire(mem)

            if self.reset_mechanism == "subtract":
                mem = (
                    input_ * self.R * (1 / (self.R * self.C)) * self.time_step
                    + (1 - (self.time_step / (self.R * self.C))) * mem
                    - reset * self.threshold
                )

            elif self.reset_mechanism == "zero":
                mem = (
                    input_ * self.R * (1 / (self.R * self.C)) * self.time_step
                    + (1 - (self.time_step / (self.R * self.C))) * mem
                    - reset
                    * (
                        (
                            input_ * self.R * (1 / (self.R * self.C)) * self.time_step
                            + (1 - (self.time_step / (self.R * self.C))) * mem
                        )
                    )
                )

            return spk, mem

        # intended for truncated-BPTT where instance variables are hidden states
        if self.init_hidden:
            if self.inhibition:
                self.spk, self.reset = self.fire_inhibition(self.batch_size, self.mem)
            else:
                self.spk, self.reset = self.fire(self.mem)

            if self.reset_mechanism == "subtract":
                self.mem = (
                    input_ * self.R * (1 / (self.R * self.C)) * self.time_step
                    + (1 - (self.time_step / (self.R * self.C))) * self.mem
                    - self.reset * self.threshold
                )

            elif self.reset_mechanism == "zero":
                self.mem = (
                    input_ * self.R * (1 / (self.R * self.C)) * self.time_step
                    + (1 - (self.time_step / (self.R * self.C))) * self.mem
                    - self.reset
                    * (
                        (
                            input_ * self.R * (1 / (self.R * self.C)) * self.time_step
                            + (1 - (self.time_step / (self.R * self.C))) * self.mem
                        )
                    )
                )

            return self.spk, self.mem

    @classmethod
    def detach_hidden(cls):
        """Returns the hidden states, detached from the current graph.
        Intended for use in truncated backpropagation through time where hidden state variables are instance variables."""

        for layer in range(len(cls.instances)):
            if isinstance(cls.instances[layer], Lapicque):
                cls.instances[layer].spk.detach_()
                cls.instances[layer].mem.detach_()

    @classmethod
    def zeros_hidden(cls):
        """Used to clear hidden state variables to zero.
        Intended for use where hidden state variables are instance variables."""

        for layer in range(len(cls.instances)):
            if isinstance(cls.instances[layer], Lapicque):
                cls.instances[layer].spk = torch.zeros_like(cls.instances[layer].spk)
                cls.instances[layer].mem = torch.zeros_like(cls.instances[layer].mem)


class Alpha(LIF):
    """
    A variant of the leaky integrate and fire neuron where membrane potential follows an alpha function.
    The time course of the membrane potential response depends on a combination of exponentials.
    In general, this causes the change in membrane potential to experience a delay with respect to an input spike.
    For :math:`U[T] > U_{\\rm thr} ⇒ S[T+1] = 1`.

    .. warning:: For a positive input current to induce a positive membrane response, ensure :math:`α > β`.

    If `reset_mechanism = "subtract"`, then :math:`I_{\\rm exc}, I_{\\rm inh}` will both have `threshold` subtracted from them whenever the neuron emits a spike:

    .. math::

            I_{\\rm exc}[t+1] = (αI_{\\rm exc}[t] + I_{\\rm in}[t+1]) - R(αI_{\\rm exc}[t] + I_{\\rm in}[t+1]) \\\\
            I_{\\rm inh}[t+1] = (βI_{\\rm inh}[t] - I_{\\rm in}[t+1]) - R(βI_{\\rm inh}[t] - I_{\\rm in}[t+1]) \\\\
            U_{\\rm residual}[t+1] = R(U[t]-U_{\\rm thr}) + U_{\\rm residual}[t]/τ_{\\rm SRM} \\\\
            U[t+1] = τ_{\\rm SRM}(I_{\\rm exc}[t+1] + I_{\\rm inh}[t+1]) + U_{\\rm residual}[t+1]

    If `reset_mechanism = "zero"`, then :math:`I_{\\rm exc}, I_{\\rm inh}` will both be set to `0` whenever the neuron emits a spike:

    .. math::

            I_{\\rm exc}[t+1] = (αI_{\\rm exc}[t] + I_{\\rm in}[t+1]) - R(αI_{\\rm exc}[t] + I_{\\rm in}[t+1]) \\\\
            I_{\\rm inh}[t+1] = (βI_{\\rm inh}[t] - I_{\\rm in}[t+1]) - R(βI_{\\rm inh}[t] - I_{\\rm in}[t+1]) \\\\
            U[t+1] = τ_{\\rm SRM}(I_{\\rm exc}[t+1] + I_{\\rm inh}[t+1])

    * :math:`I_{\\rm exc}` - Excitatory current
    * :math:`I_{\\rm inh}` - Inhibitory current
    * :math:`I_{\\rm in}` - Input current
    * :math:`U` - Membrane potential
    * :math:`U_{\\rm thr}` - Membrane threshold
    * :math:`U_{\\rm residual}` - Residual membrane potential after reset by subtraction
    * :math:`R` - Reset mechanism, :math:`R = 1` if spike occurs, otherwise :math:`R = 0`
    * :math:`α` - Excitatory current decay rate
    * :math:`β` - Inhibitory current decay rate
    * :math:`τ_{\\rm SRM} = \\frac{log(α)}{log(β)} - log(α) + 1`

    Example::

        import torch
        import torch.nn as nn
        import snntorch as snn

        alpha = 0.9
        beta = 0.8

        # Define Network
        class Net(nn.Module):
            def __init__(self):
                super().__init__()

                # initialize layers
                self.fc1 = nn.Linear(num_inputs, num_hidden)
                self.lif1 = snn.Alpha(alpha=alpha, beta=beta)
                self.fc2 = nn.Linear(num_hidden, num_outputs)
                self.lif2 = snn.Alpha(alpha=alpha, beta=beta)

            def forward(self, x, syn_exc1, syn_inh1, mem1, spk1, syn_exc2, syn_inh2, mem2):
                cur1 = self.fc1(x)
                spk1, syn_exc1, syn_inh1, mem1 = self.lif1(cur1, syn_exc1, syn_inh1, mem1)
                cur2 = self.fc2(spk1)
                spk2, syn_exc2, syn_inh2, mem2 = self.lif2(cur2, syn_exc2, syn_inh2, mem2)
                return syn_exc1, syn_inh1, mem1, spk1, syn_exc2, syn_inh2, mem2, spk2


    """

    def __init__(
        self,
        alpha,
        beta,
        threshold=1.0,
        num_inputs=False,
        spike_grad=None,
        batch_size=False,
        init_hidden=False,
        inhibition=False,
        reset_mechanism="subtract",
    ):
        super(Alpha, self).__init__(
            beta, threshold, spike_grad, inhibition, reset_mechanism
        )

        self.alpha = alpha
        self.num_inputs = num_inputs
        self.batch_size = batch_size
        self.init_hidden = init_hidden

        if self.init_hidden:
            if not self.num_inputs:
                raise ValueError(
                    "num_inputs must be specified to initialize hidden states as instance variables."
                )
            elif not self.batch_size:
                raise ValueError(
                    "batch_size must be specified to initialize hidden states as instance variables."
                )
            elif hasattr(self.num_inputs, "__iter__"):
                self.spk, self.syn_exc, self.syn_inh, self.mem = self.init_alpha(
                    batch_size=self.batch_size, *(self.num_inputs)
                )
            else:
                self.spk, self.syn_exc, self.syn_inh, self.mem = self.init_alpha(
                    batch_size, num_inputs
                )
        if self.inhibition:
            if not self.batch_size:
                raise ValueError(
                    "batch_size must be specified to enable firing inhibition."
                )

        if self.alpha <= self.beta:
            raise ValueError("alpha must be greater than beta.")

        if self.beta == 1:
            raise ValueError(
                "beta cannot be '1' otherwise ZeroDivisionError occurs: tau_srm = log(alpha)/log(beta) - log(alpha) + 1"
            )

        if reset_mechanism == "subtract":
            self.mem_residual = False

        self.tau_srm = np.log(self.alpha) / (np.log(self.beta) - np.log(self.alpha)) + 1

    def forward(self, input_, syn_exc, syn_inh, mem):

        if (
            hasattr(syn_exc, "batch_flag")
            or hasattr(syn_inh, "batch_flag")
            or hasattr(mem, "batch_flag")
        ):  # only triggered on first-pass
            syn_exc, syn_inh, mem = _SpikeTorchConv(
                syn_exc, syn_inh, mem, input_=input_
            )

        # if hidden states are passed externally
        if not self.init_hidden:

            if self.inhibition:
                spk, reset = self.fire_inhibition(self.batch_size, mem)

            else:
                spk, reset = self.fire(mem)

            # if neuron fires, subtract threhsold from neuron
            if self.reset_mechanism == "subtract":

                if self.mem_residual is False:
                    self.mem_residual = torch.zeros_like(mem)

                syn_exc = (self.alpha * syn_exc + input_) - reset * (
                    self.alpha * syn_exc + input_
                )
                syn_inh = (self.beta * syn_inh - input_) - reset * (
                    self.beta * syn_inh - input_
                )
                # The residual of (mem - threshold) decays separately
                self.mem_residual = reset * (mem - self.threshold) + (
                    self.mem_residual / self.tau_srm
                )
                mem = self.tau_srm * (syn_exc + syn_inh) + self.mem_residual

            # if neuron fires, reset membrane to zero
            elif self.reset_mechanism == "zero":
                syn_exc = (self.alpha * syn_exc + input_) - reset * (
                    self.alpha * syn_exc + input_
                )
                syn_inh = (self.beta * syn_inh - input_) - reset * (
                    self.beta * syn_inh - input_
                )
                mem = self.tau_srm * (syn_exc + syn_inh)

            return spk, syn_exc, syn_inh, mem

        # if hidden states and outputs are instance variables
        if self.init_hidden:

            if self.inhibition:
                self.spk, self.reset = self.fire_inhibition(self.batch_size, self.mem)

            else:
                self.spk, self.reset = self.fire(self.mem)

            # if neuron fires, subtract threhsold from neuron
            if self.reset_mechanism == "subtract":

                if self.mem_residual is False:
                    self.mem_residual = torch.zeros_like(mem)

                self.syn_exc = (self.alpha * self.syn_exc + input_) - self.reset * (
                    self.alpha * self.syn_exc + input_
                )
                syn_inh = (self.beta * self.syn_inh - input_) - self.reset * (
                    self.beta * self.syn_inh - input_
                )
                # The residual of (mem - threshold) decays separately
                self.mem_residual = self.reset * (self.mem - self.threshold) + (
                    self.mem_residual / self.tau_srm
                )
                self.mem = (
                    self.tau_srm * (self.syn_exc + self.syn_inh) + self.mem_residual
                )

            # if neuron fires, reset membrane to zero
            elif self.reset_mechanism == "zero":

                syn_exc = (self.alpha * syn_exc + input_) - self.reset * (
                    self.alpha * syn_exc + input_
                )
                syn_inh = (self.beta * syn_inh - input_) - self.reset * (
                    self.beta * syn_inh - input_
                )
                self.mem = self.tau_srm * (syn_exc + syn_inh)

            return self.spk, self.syn_exc, self.syn_inh, self.mem

    # cool forward function that resulted in burst firing - worth exploring

    # def forward(self, input_, syn_exc, syn_inh, mem):
    #     mem_shift = mem - self.threshold
    #     spk = self.spike_grad(mem_shift).to(device)
    #     reset = torch.zeros_like(mem)
    #     spk_idx = (mem_shift > 0)
    #     reset[spk_idx] = torch.ones_like(mem)[spk_idx]
    #
    #     syn_exc = self.alpha * syn_exc + input_
    #     syn_inh = self.beta * syn_inh - input_
    #     mem = self.tau_srm * (syn_exc + syn_inh) - reset

    # return spk, syn_exc, syn_inh, mem

    @classmethod
    def detach_hidden(cls):
        """Used to detach hidden states from the current graph.
        Intended for use in truncated backpropagation through
        time where hidden state variables are instance variables."""
        for layer in range(len(cls.instances)):
            if isinstance(cls.instances[layer], Alpha):
                cls.instances[layer].spk.detach_()
                cls.instances[layer].syn_exc.detach_()
                cls.instances[layer].syn_inh.detach_()
                cls.instances[layer].mem.detach_()

    @classmethod
    def zeros_hidden(cls):
        """Used to clear hidden state variables to zero.
        Intended for use where hidden state variables are instance variables."""
        for layer in range(len(cls.instances)):
            if isinstance(cls.instances[layer], Alpha):
                cls.instances[layer].spk = torch.zeros_like(cls.instances[layer].spk)
                cls.instances[layer].syn_exc = torch.zeros_like(
                    cls.instances[layer].syn_exc
                )
                cls.instances[layer].syn_inh = torch.zeros_like(
                    cls.instances[layer].syn_inh
                )
                cls.instances[layer].mem = torch.zeros_like(cls.instances[layer].mem)


####### Deprecated / Renamed
class Stein(LIF):  # see: Synaptic(LIF)
    """
    Stein's model of the leaky integrate and fire neuron.
    The synaptic current jumps upon spike arrival, which causes a jump in membrane potential.
    Synaptic current and membrane potential decay exponentially with rates of alpha and beta, respectively.
    For :math:`U[T] > U_{\\rm thr} ⇒ S[T+1] = 1`.

    If `reset_mechanism = "subtract"`, then :math:`U[t+1]` will have `threshold` subtracted from it whenever the neuron emits a spike:

    .. math::

            I_{\\rm syn}[t+1] = αI_{\\rm syn}[t] + I_{\\rm in}[t+1] \\\\
            U[t+1] = βU[t] + I_{\\rm syn}[t+1] - RU_{\\rm thr}

    If `reset_mechanism = "zero"`, then :math:`U[t+1]` will be set to `0` whenever the neuron emits a spike:

    .. math::

            I_{\\rm syn}[t+1] = αI_{\\rm syn}[t] + I_{\\rm in}[t+1] \\\\
            U[t+1] = βU[t] + I_{\\rm syn}[t+1] - R(βU[t] + I_{\\rm syn}[t+1])

    * :math:`I_{\\rm syn}` - Synaptic current
    * :math:`I_{\\rm in}` - Input current
    * :math:`U` - Membrane potential
    * :math:`U_{\\rm thr}` - Membrane threshold
    * :math:`R` - Reset mechanism: if active, :math:`R = 1`, otherwise :math:`R = 0`
    * :math:`α` - Synaptic current decay rate
    * :math:`β` - Membrane potential decay rate

    Example::

        import torch
        import torch.nn as nn
        import snntorch as snn

        alpha = 0.9
        beta = 0.5

        # Define Network
        class Net(nn.Module):
            def __init__(self):
                super().__init__()

                # initialize layers
                self.fc1 = nn.Linear(num_inputs, num_hidden)
                self.lif1 = snn.Stein(alpha=alpha, beta=beta)
                self.fc2 = nn.Linear(num_hidden, num_outputs)
                self.lif2 = snn.Stein(alpha=alpha, beta=beta)

            def forward(self, x, syn1, mem1, spk1, syn2, mem2):
                cur1 = self.fc1(x)
                spk1, syn1, mem1 = self.lif1(cur1, syn1, mem1)
                cur2 = self.fc2(spk1)
                spk2, syn2, mem2 = self.lif2(cur2, syn2, mem2)
                return syn1, mem1, spk1, syn2, mem2, spk2


    For further reading, see:

    *R. B. Stein (1965) A theoretical analysis of neuron variability. Biophys. J. 5, pp. 173-194.*

    *R. B. Stein (1967) Some models of neuronal variability. Biophys. J. 7. pp. 37-68.*"""

    def __init__(
        self,
        alpha,
        beta,
        threshold=1.0,
        num_inputs=False,
        spike_grad=None,
        batch_size=False,
        init_hidden=False,
        inhibition=False,
        reset_mechanism="subtract",
    ):
        super(Stein, self).__init__(
            beta, threshold, spike_grad, inhibition, reset_mechanism
        )

        print(
            "`Stein` has been deprecated and will be removed in a future version. Use `Synaptic` instead."
        )

        self.alpha = alpha
        self.num_inputs = num_inputs
        self.batch_size = batch_size
        self.init_hidden = init_hidden

        if self.init_hidden:
            if not self.num_inputs:
                raise ValueError(
                    "num_inputs must be specified to initialize hidden states as instance variables."
                )
            elif not self.batch_size:
                raise ValueError(
                    "batch_size must be specified to initialize hidden states as instance variables."
                )
            elif hasattr(self.num_inputs, "__iter__"):
                self.syn, self.mem = self.init_stein(
                    self.batch_size, *(self.num_inputs)
                )  # need to automatically call batch_size
            else:
                self.syn, self.mem = self.init_stein(self.batch_size, self.num_inputs)
        if self.inhibition:
            if not self.batch_size:
                raise ValueError(
                    "batch_size must be specified to enable firing inhibition."
                )

    def forward(self, input_, syn, mem):

        if hasattr(syn, "batch_flag") or hasattr(
            mem, "batch_flag"
        ):  # only triggered on first-pass
            syn, mem = _SpikeTorchConv(syn, mem, input_=input_)

        if not self.init_hidden:
            if self.inhibition:
                spk, reset = self.fire_inhibition(self.batch_size, mem)
            else:
                spk, reset = self.fire(mem)
            syn = self.alpha * syn + input_

            if self.reset_mechanism == "subtract":
                mem = self.beta * mem + syn - reset * self.threshold

            elif self.reset_mechanism == "zero":
                mem = self.beta * mem + syn - reset * (self.beta * mem + syn)

            return spk, syn, mem

        # intended for truncated-BPTT where instance variables are hidden states
        if self.init_hidden:
            if self.inhibition:
                self.spk, self.reset = self.fire_inhibition(self.batch_size, self.mem)
            else:
                self.spk, self.reset = self.fire(self.mem)
            self.syn = self.alpha * self.syn + input_

            if self.reset_mechanism == "subtract":
                self.mem = self.beta * self.mem + self.syn - self.reset * self.threshold

            elif self.reset_mechanism == "zero":
                self.mem = (
                    self.beta * self.mem
                    + self.syn
                    - self.reset * (self.beta * self.mem + self.syn)
                )

            return self.spk, self.syn, self.mem

    @classmethod
    def detach_hidden(cls):
        """Returns the hidden states and detaches them from the current graph.
        Intended for use in truncated backpropagation through time where hidden state variables are instance variables."""

        for layer in range(len(cls.instances)):
            if isinstance(cls.instances[layer], Stein):
                cls.instances[layer].spk.detach_()
                cls.instances[layer].syn.detach_()
                cls.instances[layer].mem.detach_()

    @classmethod
    def zeros_hidden(cls):
        """Used to clear hidden state variables to zero.
        Intended for use where hidden state variables are instance variables."""

        for layer in range(len(cls.instances)):
            if isinstance(cls.instances[layer], Stein):
                cls.instances[layer].spk = torch.zeros_like(cls.instances[layer].spk)
                cls.instances[layer].syn = torch.zeros_like(cls.instances[layer].syn)
                cls.instances[layer].mem = torch.zeros_like(cls.instances[layer].mem)
