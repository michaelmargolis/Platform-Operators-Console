#d_to_p.py  ver 2
import numpy as np
import traceback
import logging

log = logging.getLogger(__name__)

class DistanceToPressure:
    def __init__(self, nbr_columns, max_length):
        self.nbr_columns = nbr_columns
        self.loads = None    # tuple of loads
        self.max_muscle_lengths = np.full(6, max_length, dtype=float)
        self.all_d_to_p_up = None  # numpy rows of all up values
        self.all_d_to_p_down = None  # numpy rows of all down values
        self.d_to_p_up = None  # numpy rows of interpolated up values
        self.d_to_p_down = None  # numpy rows of interpolated down values
        self.d_to_p = None # self.d_to_p[0] → up table, self.d_to_p[1] → down table
        self.selected_load = None
        self.threshold = 5.0
        self.blend_distance = 5.0
        self.turning_compressions = None
        self.active_row = None

    def _get_loads(self, csv_path):
        # returns first data row, loads tuple (or none if invalid data)
        with open(csv_path, 'r') as file:
            skiped_lines = 0
            for line in file:
                skiped_lines += 1
                if line.startswith('# weights'):
                    fields = line.strip().split(',')[1:] # Extract fields after '# weights'
                    last_non_empty_index = next((i for i in reversed(range(len(fields))) if fields[i] != ''), -1)
                    fields = fields[:last_non_empty_index + 1]
                    loads_tuple = tuple(map(int, fields))
                    return skiped_lines, loads_tuple
            return None, None
   
    def _interpolate_load(self, d_to_p, l):
        """Interpolate d_to_p rows between two nearest loads."""
        d_to_p = np.asarray(d_to_p)

        # Handle edge cases
        if l <= self.loads[0]:
            return d_to_p[0]
        if l >= self.loads[-1]:
            return d_to_p[-1]

        # Find lower/upper bounds 
        idx_upper = np.searchsorted(self.loads, l)
        idx_lower = idx_upper - 1

        l_lower, l_upper = self.loads[idx_lower], self.loads[idx_upper]
        d_lower, d_upper = d_to_p[idx_lower], d_to_p[idx_upper]

        interpolation_factor = (l - l_lower) / (l_upper - l_lower)
        interpolated = (1 - interpolation_factor) * d_lower + interpolation_factor * d_upper
        return interpolated
        
    def load_data(self, csv_path):
        log.info("Using distance to Pressure file: %s" , csv_path)
        try:
            skiped_lines, loads = self._get_loads(csv_path)
            # print(skiped_lines, loads)
            if loads:
                self.loads = np.asarray(loads)
                # Ensure sorted loads
                if not np.all(np.diff(loads) > 0):
                    raise ValueError("loads must be in strictly ascending order.")
                d_to_p = np.loadtxt(csv_path, delimiter=',', skiprows=skiped_lines, dtype=float)
                if d_to_p.ndim != 2:
                    raise ValueError("D-to-P data must contain multiple rows")
                # print(d_to_p, d_to_p.shape[1])
                if d_to_p.shape[1] != self.nbr_columns:
                    raise ValueError(f"In {csv_path} expected {int(self.nbr_columns)} distance values, but found {d_to_p.shape[1]}")
                if d_to_p.shape[0] != 2 * len(self.loads):
                    raise ValueError(
                        f"In {csv_path} expected {2 * len(self.loads)} pressure rows "
                        f"for {len(self.loads)} loads, but found {d_to_p.shape[0]}"
                    )
                self.all_d_to_p_up, self.all_d_to_p_down = np.split(d_to_p, 2)
                # print( "up", self.all_d_to_p_up)
                # print("down",  self.all_d_to_p_down)
                if self.all_d_to_p_up.shape[0] != self.all_d_to_p_down.shape[0]:
                    raise ValueError("Up and down DtoP rows don't match")
                self.rows = self.all_d_to_p_up.shape[0]
                if self.nbr_columns != self.all_d_to_p_up.shape[1]:
                    print(f"number of columns {self.all_d_to_p_up.shape[1]}, expected {self.nbr_columns} " )
                return True
            return False    
        except Exception as e:
            log.error("Error loading file: %s\n%s", e, traceback.format_exc())
            raise
            
    def set_load(self, load):
        """Set load and calculate interpolation parameters."""
        self.selected_load = float(load)
        self.d_to_p_up = self._interpolate_load(self.all_d_to_p_up, load)
        self.d_to_p_down = self._interpolate_load(self.all_d_to_p_down, load)
        self.d_to_p = np.stack([self.d_to_p_up, self.d_to_p_down], axis=0)
       #  print(f"in set_load, d_to_p stack is: {self.d_to_p}")

    def muscle_length_to_pressure(self, muscle_lengths):
        muscle_lengths = np.asarray(muscle_lengths, dtype=float)
        if muscle_lengths.shape != self.max_muscle_lengths.shape:
            raise ValueError("Invalid number of muscle lengths")
        muscle_compressions =  self.max_muscle_lengths - muscle_lengths
        return self.muscle_compression_to_pressure(muscle_compressions)
    
    def _lookup_branch_pressures(self, compressions):
        """Interpolate both pressure branches at fractional-mm compressions."""
        lower = np.floor(compressions).astype(int)
        upper = np.ceil(compressions).astype(int)
        fraction = compressions - lower
        return (
            self.d_to_p[:, lower] * (1.0 - fraction)
            + self.d_to_p[:, upper] * fraction
        )

    def muscle_compression_to_pressure(self, compressions):
        """
        Convert six compression values (mm) to pressures using a 2-row hysteresis table.

        self.d_to_p : shape [2, N]  (row 0 = increasing-contraction branch,
                                     row 1 = decreasing-contraction branch)
        self.threshold : float      (reversal deadband in mm)
        self.blend_distance : float (distance over which branches are blended)

        Returns
        -------
        pressures : np.ndarray  shape (6,)  – one pressure per muscle
        """
        if self.d_to_p is None:
            raise RuntimeError("D-to-P load has not been selected")

        compressions = np.asarray(compressions, dtype=float)
        if compressions.shape != self.max_muscle_lengths.shape:
            raise ValueError("Invalid number of muscle compressions")
        compressions = np.clip(compressions, 0.0, self.d_to_p.shape[1] - 1)
        branch_pressures = self._lookup_branch_pressures(compressions)

        # Initial pressurisation contracts the muscles, so start on row 0.
        if self.active_row is None:
            self.active_row = np.zeros(6, dtype=int)
            self.turning_compressions = compressions.copy()
            return np.rint(branch_pressures[0]).astype(int)

        pressures = np.empty(6, dtype=float)
        for index, compression in enumerate(compressions):
            if self.active_row[index] == 0:
                self.turning_compressions[index] = max(
                    self.turning_compressions[index], compression
                )
                reversal = self.turning_compressions[index] - compression
                blend = np.clip(
                    (reversal - self.threshold) / self.blend_distance, 0.0, 1.0
                )
                pressures[index] = (
                    (1.0 - blend) * branch_pressures[0, index]
                    + blend * branch_pressures[1, index]
                )
                if blend >= 1.0:
                    self.active_row[index] = 1
                    self.turning_compressions[index] = compression
            else:
                self.turning_compressions[index] = min(
                    self.turning_compressions[index], compression
                )
                reversal = compression - self.turning_compressions[index]
                blend = np.clip(
                    (reversal - self.threshold) / self.blend_distance, 0.0, 1.0
                )
                pressures[index] = (
                    (1.0 - blend) * branch_pressures[1, index]
                    + blend * branch_pressures[0, index]
                )
                if blend >= 1.0:
                    self.active_row[index] = 0
                    self.turning_compressions[index] = compression

        return np.rint(pressures).astype(int)

# ---------------------------------------------------------
# Test harness
# ---------------------------------------------------------
if __name__ == '__main__':
    max_range = 250
    max_length = 1000
    d2p = DistanceToPressure(max_range+1, max_length)
    if d2p.load_data("wheelchair_DtoP.csv"):
        d2p.set_load(24)  # Set test load

    print(d2p.d_to_p) 
    while True:
        user_input = input("Enter 6 lengths separated by commas or 'l,<load>' to set load (empty input to quit): ").strip()

        if not user_input:
            break

        if user_input.lower().startswith('l,'):
            try:
                load = int(user_input.split(',')[1])
                d2p.set_load(load) 
                print(f"Load updated to: {load}kg")
            except (IndexError, ValueError):
                print("Invalid load format. Use l,<number> (e.g., l,35)")
            continue

        try:
            lengths = [int(val) for val in user_input.split(',')]
            if len(lengths) != 6:
                print("Please enter exactly 6 numbers.")
                continue

            pressures = d2p.muscle_length_to_pressure(lengths)
            print(f"Input lengths: {lengths}")
            print(f"Resulting pressures: {pressures}")

        except ValueError:
            print("Invalid input. Please enter numbers separated by commas.")
