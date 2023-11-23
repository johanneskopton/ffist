import matplotlib.pyplot as plt
import numpy as np
import sklearn.metrics
import sklearn.model_selection
from scipy.optimize import minimize

from minkowski import Data
from minkowski.utils import get_distances
from minkowski.utils import histogram2d
from minkowski.variogram_models import calc_weights
from minkowski.variogram_models import get_initial_parameters
from minkowski.variogram_models import prediction_grid
from minkowski.variogram_models import variogram_model_dict
from minkowski.variogram_models import weighted_mean_square_error
plt.style.use("ggplot")


class Predictor:
    def __init__(
        self,
        data: Data,
        covariate_model: sklearn.base.ClassifierMixin,
        cv_splits: int = 5,
    ):
        self._data = data
        self._cv_splits = cv_splits

        self._cov_model = covariate_model
        self._X = self._data.get_training_covariates()
        self._y = self._data.predictand

        self._cross_val_res = None
        self._variogram = None
        self._variogram_bins_space = None
        self._variogram_bins_time = None

        self._is_binary = self._data.predictand.dtype == bool

    def fit_covariate_model(self, train_idxs=None):
        if train_idxs is None:
            self._cov_model.fit(self._X, self._y)
        else:
            self._cov_model.fit(self._X[train_idxs, :], self._y[train_idxs])

    def get_covariate_probability(self, idxs=slice(None)):
        if self._is_binary:
            return self._cov_model.predict_proba(self._X[idxs])[:, 1]
        else:
            return self._cov_model.predict(self._X[idxs])

    def predict_covariate_probability(self, df):
        X = self._data.prepare_covariates(df)

        if self._is_binary:
            return self._cov_model.predict_proba(X)[:, 1]
        else:
            return self._cov_model.predict(X)

    def get_residuals(self, idxs=slice(None)):
        return self.get_covariate_probability(idxs) - self._y[idxs]

    def calc_cross_validation(self):
        cv = sklearn.model_selection.TimeSeriesSplit(n_splits=self._cv_splits)
        ground_truth = []
        prediction = []
        for fold, (train, test) in enumerate(cv.split(self._X, self._y)):
            self.fit_covariate_model(train)
            ground_truth.append(self._y[test])
            prediction.append(self.get_covariate_probability(test))

        self._cross_val_res = ground_truth, prediction

    def get_cross_val_metric(self, metric):
        if self._cross_val_res is None:
            self.calc_cross_validation()

        ground_truth, prediction = self._cross_val_res

        res = []
        for i in range(self._cv_splits):
            res.append(metric(ground_truth[i], prediction[i]))
        return res

    def calc_empirical_variogram(
        self,
        idxs=slice(None, None),
        space_dist_max=3,
        time_dist_max=10,
        n_space_bins=10,
        n_time_bins=10,
        el_max=1e6,
    ):
        residuals = self.get_residuals(idxs)
        space_coords = self._data.space_coords[idxs, :]
        time_coords = self._data.time_coords[idxs]

        space_lags, time_lags, sq_val_delta = get_distances(
            space_coords,
            time_coords,
            residuals,
            space_dist_max,
            time_dist_max,
            el_max,
        )

        space_range = (0, space_dist_max)
        time_range = (0, time_dist_max)
        hist, samples_per_bin, bin_width_space, bin_width_time = histogram2d(
            space_lags,
            time_lags,
            n_space_bins,
            n_time_bins,
            space_range,
            time_range,
            sq_val_delta,
        )

        # I think this "/2" is necessary, because in samples_per_bin are only
        # n^2/2 samples in total
        variogram = np.divide(
            hist,
            samples_per_bin,
            out=np.ones_like(hist) * np.nan,
            where=samples_per_bin != 0,
        ) / 2

        bins_space = np.arange(n_space_bins+1) * bin_width_space
        bins_space = ((bins_space[:-1] + bins_space[1:])/2)

        bins_time = np.arange(n_time_bins+1) * bin_width_time
        bins_time = ((bins_time[:-1] + bins_time[1:])/2)

        self._variogram = variogram
        self._variogram_bins_space = bins_space
        self._variogram_bins_time = bins_time
        self._variogram_samples_per_bin = samples_per_bin

    def fit_variogram_model(
        self,
        st_model="sum_metric",
        space_model="spherical",
        time_model="spherical",
        metric_model="spherical",
        plot_anisotropy=False,
    ):
        slope_space = np.polynomial.polynomial.polyfit(
            self._variogram_bins_space, self._variogram[:, 0], deg=1,
        )[1]
        slope_time = np.polynomial.polynomial.polyfit(
            self._variogram_bins_time, self._variogram[0, :], deg=1,
        )[1]
        ani = slope_time / slope_space

        if plot_anisotropy:
            fig, ax = plt.subplots()
            ax.scatter(
                self._variogram_bins_space/ani,
                self._variogram[:, 0], label="rescaled spatial",
            )
            ax.scatter(
                self._variogram_bins_time,
                self._variogram[0, :], label="temporal",
            )
            ax.set_xlabel("lag")
            ax.set_ylabel("variance")
            ax.set_title("Anisotropy coefficient: {:.3}".format(ani))
            ax.legend()
            plt.show()

        weights = calc_weights(
            self._variogram_bins_space,
            self._variogram_bins_time,
            ani,
            self._variogram_samples_per_bin,
        )

        initial_params = get_initial_parameters(
            st_model,
            self._variogram,
            self._variogram_bins_space[-1],
            self._variogram_bins_time[-1],
            ani,
        )

        variogram_fit = minimize(
            weighted_mean_square_error,
            initial_params,
            args=(
                variogram_model_dict[st_model],
                variogram_model_dict[space_model],
                variogram_model_dict[time_model],
                variogram_model_dict[metric_model],
                self._variogram_bins_space,
                self._variogram_bins_time,
                self._variogram,
                weights,
            ),
            method="Nelder-Mead",
            options={"maxiter": 10000},
        )

        self._variogram_fit = variogram_fit
        self._variogram_models = [
            st_model, space_model, time_model, metric_model,
        ]

    def get_variogram_model_grid(self):
        if self._variogram_fit is None:
            raise ValueError("Fit variogram model first.")

        st_model, space_model, time_model, metric_model = \
            self._variogram_models

        grid = prediction_grid(
            self._variogram_fit.x,
            variogram_model_dict[st_model],
            variogram_model_dict[space_model],
            variogram_model_dict[time_model],
            variogram_model_dict[metric_model],
            self._variogram_bins_space,
            self._variogram_bins_time,
        )
        return grid

    def plot_cross_validation_roc(self):
        if self._cross_val_res is None:
            self.calc_cross_validation()

        tprs = []
        aucs = []
        mean_fpr = np.linspace(0, 1, 100)
        fig, ax = plt.subplots(figsize=(6, 6))

        ground_truth, pred_probability, pred = self._cross_val_res

        for fold in range(self._cv_splits):
            viz = sklearn.metrics.RocCurveDisplay.from_predictions(
                ground_truth[fold],
                pred_probability[fold],
                name=f"ROC fold {fold}",
                alpha=0.3,
                lw=1,
                ax=ax,
                # commented out for compatibility with older sklearn and cuml
                # plot_chance_level=(fold == self._cv_splits - 1),
            )
            interp_tpr = np.interp(mean_fpr, viz.fpr, viz.tpr)
            interp_tpr[0] = 0.0
            tprs.append(interp_tpr)
            aucs.append(viz.roc_auc)

        mean_tpr = np.mean(tprs, axis=0)
        mean_tpr[-1] = 1.0
        mean_auc = sklearn.metrics.auc(mean_fpr, mean_tpr)
        std_auc = np.std(aucs)
        ax.plot(
            mean_fpr,
            mean_tpr,
            color="b",
            label=r"Mean ROC (AUC = %0.2f $\pm$ %0.2f)" % (
                mean_auc, std_auc,
            ),
            lw=2,
            alpha=0.8,
        )

        std_tpr = np.std(tprs, axis=0)
        tprs_upper = np.minimum(mean_tpr + std_tpr, 1)
        tprs_lower = np.maximum(mean_tpr - std_tpr, 0)
        ax.fill_between(
            mean_fpr,
            tprs_lower,
            tprs_upper,
            color="grey",
            alpha=0.2,
            label=r"$\pm$ 1 std. dev.",
        )

        ax.set(
            xlim=[-0.05, 1.05],
            ylim=[-0.05, 1.05],
            xlabel="False Positive Rate",
            ylabel="True Positive Rate",
            title="Mean ROC curve with variability\n(TimeSeriesPrediction)",
        )
        ax.axis("square")
        ax.legend(loc="lower right")
        plt.show()

    def plot_empirical_variogram(
        self,
        fig=None,
        ax=None,
        vrange=(None, None),
        title="",
    ):
        if self._variogram is None:
            raise ValueError("Calc variogram first.")
        X, Y = np.meshgrid(
            self._variogram_bins_space,
            self._variogram_bins_time,
        )
        if ax is None:
            standalone = True
            fig = plt.figure(figsize=(5, 5))
            ax = plt.axes(projection='3d')
        else:
            standalone = False
        vmin, vmax = vrange
        plot = ax.plot_surface(
            X, Y, self._variogram.T, vmin=vmin, vmax=vmax,
            cmap="plasma", edgecolor="black", linewidth=0.5,
        )
        ax.view_init(elev=35., azim=225)
        ax.grid(False)
        ax.set_title(title)
        ax.set_xlabel("space lag")
        ax.set_ylabel("time lag")
        fig.colorbar(plot, fraction=0.044, pad=0.04)

        if standalone:
            plt.show()
