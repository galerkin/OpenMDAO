
from collections import OrderedDict

from openmdao.core.group import Group
from openmdao.core.mpiwrap import MPI

class ParallelGroup(Group):
    def apply_nonlinear(self, params, unknowns, resids):
        """ Evaluates the residuals of our children systems.

        Parameters
        ----------
        params : `VecWrapper`
            `VecWrapper` containing parameters. (p)

        unknowns : `VecWrapper`
            `VecWrapper`  containing outputs and states. (u)

        resids : `VecWrapper`
            `VecWrapper`  containing residuals. (r)
        """

        # full scatter
        self._varmanager._transfer_data()

        for name, sub in self.subsystems(local=True):
            sub.apply_nonlinear(sub.params, sub.unknowns, sub.resids)

    def children_solve_nonlinear(self):
        """Loops over our children systems and asks them to solve."""

        # full scatter
        self._varmanager._transfer_data()

        for name, sub in self.subsystems(local=True):
            sub.solve_nonlinear(sub.params, sub.unknowns, sub.resids)

    def get_req_procs(self):
        """
        Returns
        -------
        tuple
            A tuple of the form (min_procs, max_procs), indicating the min and max
            processors usable by this `ParallelGroup`.
        """
        min_procs = 1
        max_procs = 1

        for name, sub in self.subsystems():
            sub_min, sub_max = sub.get_req_procs()
            min_procs += sub_min
            if max_procs is not None:
                if sub_max is None:
                    max_procs = None
                else:
                    max_procs += sub_max

        return (min_procs, max_procs)

    def _setup_communicators(self, comm):
        """
        Assign communicator to this `ParallelGroup` and all of its subsystems.

        Parameters
        ----------
        comm : an MPI communicator (real or fake)
            The communicator being offered by the parent system.
        """
        if not MPI:
            super(ParallelGroup, self)._setup_communicators(comm)
            return

        self.comm = comm
        size = comm.size
        rank = comm.rank

        subsystems = []
        requested_procs = []
        max_req_procs = []
        for _, system in self.subsystems():
            subsystems.append(system)
            mincpu, maxcpu = system.get_req_procs()
            assert(mincpu > 0)
            requested_procs.append(mincpu)
            max_req_procs.append(maxcpu)

        assigned_procs = [0]*len(requested_procs)

        assigned = 0

        requested = sum(requested_procs)

        mn, mx = self.get_req_procs()
        if mx is None:
            limit = size
            max_requested = size
        else:
            max_requested = sum(max_req_procs)
            limit = min(size, max_requested)

        # first, just use simple round robin assignment of requested CPUs
        # until everybody has what they asked for or we run out
        if requested:
            while assigned < limit:
                for i, system in enumerate(subsystems):
                    if max_req_procs[i] is None or \
                                   assigned_procs[i] < max_req_procs[i]:
                        assigned_procs[i] += 1
                        assigned += 1
                        if assigned == limit:
                            break

        self._local_subsystems = OrderedDict()

        for i,sub in enumerate(subsystems):
            if requested_procs[i] > assigned_procs[i]:
                raise RuntimeError("subsystem group %s requested %d processors but got %s" %
                                   (sub.name, requested_procs[i], assigned_procs[i]))

        # a 'color' is assigned to each subsystem, with
        # an entry for each processor it will be given
        # e.g. [0, 1, 1, 1, 1, 2, 2, 3, 3, 3, UND, UND]
        color = []
        for i, procs in enumerate(assigned_procs):
            color.extend([i]*procs)

        if size > assigned:
            color.extend([MPI.UNDEFINED]*(size-assigned))

        # create a sub-communicator for each color and
        # get the one assigned to our color/process
        rank_color = color[rank]
        sub_comm = comm.Split(rank_color)

        if sub_comm == MPI.COMM_NULL:
            return

        for i,sub in enumerate(subsystems):
            if i == rank_color:
                self._local_subsystems[sub.name] = sub
            else:
                self._add_remote_subsystem(sub)

        for sub in self._local_subsystems.values():
            sub._setup_communicators(sub_comm)
