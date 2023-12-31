import numba as nb
import numpy as np


@nb.njit(fastmath=True)
def calc_distance_matrix_1d(vec):
    res = np.empty((len(vec), len(vec)), dtype=vec.dtype)
    for i in range(len(vec)):
        for j in range(len(vec)):
            res[i, j] = np.abs(vec[i]-vec[j])
    return res


@nb.njit(fastmath=True)
def calc_distance_matrix_2d(vec):
    res = np.empty((vec.shape[0], vec.shape[0]), dtype=vec.dtype)
    for i in range(vec.shape[0]):
        for j in range(vec.shape[0]):
            res[i, j] = np.sqrt(
                (vec[i, 0]-vec[j, 0])**2 +
                (vec[i, 1]-vec[j, 1])**2,
            )
    return res


@nb.njit(fastmath=True)
def get_distances(space, time, val, space_max, time_max, el_max):
    # shuffle data, so that we get a random sample, it el_max is too small
    # to fit all relevant elements
    shuffle_idxs = np.arange(len(val))
    np.random.shuffle(shuffle_idxs)

    space = space[shuffle_idxs, :]
    time = time[shuffle_idxs]
    val = val[shuffle_idxs]

    el_max = int(el_max)
    space_lags = np.empty(el_max, dtype=space.dtype)
    time_lags = np.empty(el_max, dtype=time.dtype)
    sq_val_deltas = np.empty(el_max, dtype=val.dtype)
    ii = 0
    for i in range(len(val)):
        for j in range(i+1, len(val)):
            space_lag = np.sqrt(
                np.square(space[i, 0]-space[j, 0]) +
                np.square(space[i, 1]-space[j, 1]),
            )
            if space_lag > space_max:
                continue
            time_lag = np.abs(time[i]-time[j])
            if time_lag > time_max:
                continue
            sq_val_delta = np.square(val[i]-val[j])

            space_lags[ii] = space_lag
            time_lags[ii] = time_lag
            sq_val_deltas[ii] = sq_val_delta

            ii += 1
            if ii >= el_max:
                print("not all relevant elements fit in matrix")
                return space_lags, time_lags, sq_val_deltas

    return space_lags[:ii], time_lags[:ii], sq_val_deltas[:ii]


@nb.njit(fastmath=True)
def histogram2d(
    x_coords, y_coords, num_bins_x, num_bins_y, x_range, y_range,
    values,
):
    bin_width_x = (x_range[1] - x_range[0]) / num_bins_x
    bin_width_y = (y_range[1] - y_range[0]) / num_bins_y
    hist = np.zeros((num_bins_x, num_bins_y), dtype=np.float64)
    norm = np.zeros((num_bins_x, num_bins_y), dtype=np.float64)

    for i in range(len(x_coords)):
        x = x_coords[i]
        y = y_coords[i]
        value = values[i]

        bin_x = int((x - x_range[0]) / bin_width_x)
        bin_y = int((y - y_range[0]) / bin_width_y)

        if 0 <= bin_x < num_bins_x and 0 <= bin_y < num_bins_y:
            hist[bin_x, bin_y] += value
            norm[bin_x, bin_y] += 1

    return hist, norm, bin_width_x, bin_width_y
