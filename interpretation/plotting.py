import itertools
import os

import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib import pyplot as plt
from natsort import natsorted
from scipy import optimize as optimization
from sklearn.metrics import roc_curve, roc_auc_score
from sklearn.utils import resample


def fourPL(x, A, B, C, D):
    """4 parameter logistic function"""
    return ((A-D)/(1.0+((x/C)**(B))) + D)


def fit2df(df, model):
    """fit model to x, y data in dataframe.
    Return a dataframe with fit x, y for plotting
    """
    sera = df['serum ID'].unique()
    antigens = df['antigen'].unique()
    secondaries = df['secondary ID'].unique()

    keys = itertools.product(sera, antigens, secondaries)
    df_fit = pd.DataFrame(columns=df.columns)
    for serum, antigen, secondary in keys:
        print('Fitting {}, {}...'.format(serum, antigen))
        sec_dilu_df = df[(df['serum ID']== serum) &
                    (df['antigen'] == antigen) &
                    (df['secondary ID'] == secondary)]
        sec_dilutions = sec_dilu_df['secondary dilution'].unique()
        for sec_dilution in sec_dilutions:
            sub_df = sec_dilu_df[(sec_dilu_df['secondary dilution'] == sec_dilution)].reset_index(drop=True)
            df_fit_temp = pd.DataFrame()
            guess = [0, 1, 5e-4, 1]
            xdata = sub_df['serum dilution'].to_numpy()
            ydata = sub_df['OD'].to_numpy()
            params, params_covariance = optimization.curve_fit(model, xdata, ydata, guess, bounds=(0, np.inf), maxfev=1e5)
            x_input = np.logspace(np.log10(np.min(xdata)), np.log10(np.max(xdata)), 50)
            y_fit = fourPL(x_input, *params)

            df_fit_temp['serum dilution'] = x_input
            df_fit_temp['OD'] = y_fit
            df_fit_temp['serum ID'] = ' '.join([serum, 'fit'])
            sub_df_expand = pd.concat(
                [sub_df.loc[[0], ['antigen',
                             'serum type',
                             'secondary ID',
                             'secondary dilution',
                             'pipeline']]] * len(df_fit_temp.index), axis=0).reset_index(drop=True)
            df_fit_temp = pd.concat([df_fit_temp, sub_df_expand], axis=1)
            df_fit = df_fit.append(df_fit_temp)
    print('4PL fitting finished')
    return df_fit

def roc_ci(df, ci):
    """Helper function to compute mean and confidence intervals
    from the bootstrapped distribution"""
    tpr_mean = df['tpr'].mean()
    cis = sns.utils.ci(df['tpr'], ci).tolist()
    return pd.Series([tpr_mean] + cis, ['True positive rate', 'ci_low', 'ci_high'])

def roc_from_df(df, ci=None):
    """
    Helper function to compute ROC curves using pandas.groupby(). Confidence intervals
    are computed using bootstrapping with stratified resampling
    :param dataframe df: dataframe containing serum OD info
    :param int or None ci: Confidence interval of the ROC curves in the unit of percent
    (95 would be 95%). If None, confidence intervals are not computed.
    :return dataframe rate_df: dataframe contains ROC curves for each condition
    """
    aucs = []
    n_btstp = 1000
    s = {}
    fprs = []
    tprs = []
    thrs = []
    y_test = df['serum type']
    y_prob = df['OD']
    s['False positive rate'], s['True positive rate'], s['threshold'] = \
        roc_curve(y_test, y_prob, pos_label='positive', drop_intermediate=False)
    try:
        s['AUC'] = [roc_auc_score(y_test, y_prob)] * len(s['False positive rate'])
    except ValueError as err:
        print('antigen {} only has {} serum type. {}'.
              format(df['antigen'].unique()[0], y_test.unique()[0], err))
        s['AUC'] = [np.nan] * len(s['False positive rate'])
    if ci is None:
        return pd.Series(s)
    else:
        for i in range(n_btstp):
            df_rsmpl = resample(df, n_samples=len(df), stratify=df['serum type'])
            y_test = df_rsmpl['serum type']
            y_prob = df_rsmpl['OD']
            fpr_tmp, tpr_tmp, thr_tmp = \
                roc_curve(y_test, y_prob, pos_label='positive', drop_intermediate=False)
            fprs += fpr_tmp.tolist()
            tprs += tpr_tmp.tolist()
            thrs += thr_tmp.tolist()
            try:
                aucs.append(roc_auc_score(y_test, y_prob))
            except ValueError as err:
                aucs.append(np.nan)
        rate_df = pd.DataFrame({'False positive rate': fprs, 'tpr': tprs})
        rate_df = rate_df.groupby('False positive rate').apply(
            lambda x: roc_ci(x, ci)).reset_index()
        if len(rate_df) > 0:
            rate_df = pd.concat([pd.DataFrame(data=np.zeros((1, 4)), columns=rate_df.columns), rate_df]) # add the origin corresponding to maximum threshold
        auc_low, auc_high = sns.utils.ci(aucs, ci)
        rate_df['auc_ci_low'] = auc_low
        rate_df['auc_ci_high'] = auc_high
        return rate_df

def get_roc_df(df, ci=None):
    """
    Generate ROC curves for serum samples
    :param dataframe df: dataframe containing serum OD info
    :param int or None ci: Confidence interval of the ROC curves in the unit of percent
    (95 would be 95%). If None, confidence intervals are not computed.
    :return dataframe roc_df: dataframe contains ROC curves for each condition
    """
    df = df[df['serum type'].isin(['positive', 'negative'])]
    roc_df = df[['antigen',
                 'serum type',
                 'secondary ID',
                 'secondary dilution',
                 'OD',
                 'pipeline']]
    roc_df = roc_df.groupby(['antigen',
                             'secondary ID',
                             'secondary dilution',
                             'pipeline']).apply(lambda x: roc_from_df(x, ci))
    # roc_df = roc_df.reset_index()
    roc_df = roc_df.apply(pd.Series.explode).astype(float).reset_index()
    roc_df.dropna(inplace=True)
    return roc_df

def roc_plot(x, y, **kwargs):
    """ helper function for making ROC plots with
    seaborn FacetGrid
    """

    df = kwargs.pop('data')
    ci = kwargs.pop('ci')
    ax = plt.gca()
    df.plot(x=x, y=y, ax=ax, legend=False)
    if ci is not None:
        ax.fill_between(df[x], df['ci_low'], df['ci_high'], alpha=0.2)
        auc_low = df['auc_ci_low'].unique()[0]
        auc_high = df['auc_ci_high'].unique()[0]
        ax.text(0.4, 0.15, 'AUC={:.3f}-{:.3f}'.format(auc_low, auc_high), fontsize=12)
    else:
        auc = df['AUC'].unique()[0]
        ax.text(0.6, 0.15, 'AUC={:.3f}'.format(auc), fontsize=12)

def roc_plot_grid(df, fig_path, fig_name, ext='png', hue=None,
                  col_wrap=3, ci=95, tpr=None, fpr=None):
    """
    Generate ROC plots for each antigen
    :param dataframe df: dataframe containing serum OD info
    :param str fig_path: dir to save the plots
    :param str fig_name: name of the figure file
    :param str ext: figure file extension
    :param str hue: attribute to be plotted with different colors
    :param int col_wrap: number of columns in the facetgrid
    :param int ci: Confidence interval of the ROC curves in the unit of percent
    (95 would be 95%). If None, confidence intervals are not computed.
    :param float tpr: True positive rate at which the false positive rate is shown on the curve
    :param float fpr: False positive rate at which the true positive rate is shown on the curve
    :return:
    """
    assert tpr is None or fpr is None, \
        'Specify either true positive rate or false positive rate, not both.'
    # Plot ROC curves
    antigens = natsorted(df['antigen'].unique())
    sns.set_context("notebook")
    assert not df.empty, 'Plotting dataframe is empty. Please check the plotting keys'
    palette = sns.color_palette(n_colors=len(df[hue].unique()))
    print('Computing ROC curves...')
    roc_df = get_roc_df(df, ci=ci)
    g = sns.FacetGrid(roc_df, hue=hue, col="antigen", col_order=antigens, col_wrap=col_wrap, aspect=1,
                      xlim=(-0.05, 1), ylim=(0, 1.05))
                      # hue_kws={'linestyle': ['-', '--', '-.', ':']})
    g = (g.map_dataframe(roc_plot, 'False positive rate', 'True positive rate', ci=ci))
    for antigen, ax in zip(antigens, g.axes.flat):
        sub_df = roc_df[roc_df['antigen'] == antigen]
        tpr = np.interp(fpr, sub_df['False positive rate'], sub_df['True positive rate'])
        ax.plot([fpr, fpr], [0, tpr], linewidth=1, color='g', linestyle='--', alpha=1)
        ax.plot([-0.05, fpr], [tpr, tpr], linewidth=1, color='g', linestyle='--', alpha=1)
        ax.set_title(antigen)
        ax.text(fpr + 0.05, tpr - 0.2, 'sensitivity={:.3f}\nspecificity={:.3f}'.format(tpr, 1-fpr),
                fontsize=12, color='g')  # add text
    plt.savefig(os.path.join(fig_path, '.'.join([fig_name, ext])),
                             dpi=300, bbox_inches='tight')
    plt.close()
    return roc_df

def thr_plot_grid(roc_df, fig_path, fig_name, ext, col_wrap=3):
    """
    Generate ROC plots with thresholds for each antigen
    :param roc_df:
    :param fig_path:
    :param fig_name:
    :param ext:
    :param col_wrap:
    :return:
    """
    # Plot ROC curves
    hue = 'category'
    antigens = natsorted(roc_df['antigen'].unique())
    sns.set_context("notebook")
    assert not roc_df.empty, 'Plotting dataframe is empty. Please check the plotting keys'
    palette = sns.color_palette(n_colors=len(roc_df[hue].unique()))
    print('plotting ROC curves...')
    g = sns.FacetGrid(roc_df, hue=hue, col="antigen", col_order=antigens, col_wrap=col_wrap, aspect=1,
                      hue_kws={'linestyle': ['-', '--', '-.', ':']})
    g = (g.map(plt.plot, 'threshold', 'rate').add_legend())
    # g = (g.map_dataframe(roc_plot, 'False positive rate', 'True positive rate', y2='threshold'))
    for antigen, ax in zip(antigens, g.axes.flat):
        sub_df = roc_df[roc_df['antigen'] == antigen]
        auc = sub_df['AUC'].unique()[0]
        ax.set_title(antigen)
        ax.text(1.5, 0.2, 'AUC={:.3f}'.format(auc), fontsize=12)  # add text
        # ax2 = ax.twinx()
        # sub_df.plot(x='False positive rate', y='threshold', ax=ax2)
    plt.savefig(os.path.join(fig_path, '.'.join([fig_name, ext])),
                             dpi=300, bbox_inches='tight')
    plt.close()


def scatter_plot(df,
                 x_col,
                 y_col,
                 title,
                 output_path,
                 output_fname,
                 xlim=None,
                 ylim=None,
                 alpha=1):
    """x, y scatter_plot"""
    diff_df = df[y_col] - df[x_col]
    me = diff_df.mean()
    mae = diff_df.abs().mean()
    fig = plt.figure()
    fig.set_size_inches((6, 6))
    ax = sns.scatterplot(x=x_col, y=y_col, data=df, alpha=alpha)
    plt.title(title)
    if xlim is None:
        xlim = ax.get_xlim()
    if ylim is None:
        ylim = ax.get_xlim()
    ax.set_xlim(left=xlim[0], right=xlim[1])
    ax.set_ylim(bottom=ylim[0], top=ylim[1])
    xfit = np.linspace(xlim[0], xlim[1], 2)
    plt.plot(xfit, xfit, linewidth=5, color='k', linestyle='--', alpha=0.5)
    ax.text(0.7 * xlim[1], 0.15 * ylim[1], 'Bias={:.3f}'.format(me), fontsize=16)
    ax.text(0.7 * xlim[1], 0.1 * ylim[1], 'Noise={:.3f}'.format(mae), fontsize=16)
    plt.savefig(os.path.join(output_path, ''.join([output_fname, '.jpg'])),
                dpi=300, bbox_inches='tight')
    plt.close()


def joint_plot(df_ori,
            x_col,
            y_col,
            hue,
            title,
            output_path,
            output_fname,
            bw='scott',
            n_levels=60,
            xlim=None,
            ylim=None,
            ):
    """ Join distribution plot"""

    # g = sns.JointGrid(x=x_col, y=y_col, data=df)
    #                   # xlim=(0, 50), ylim=(0, 8))
    # g = g.plot_joint(sns.kdeplot, cmap="Purples_d")
    # g = g.plot_marginals(sns.kdeplot, color="m", shade=True)
    df = df_ori.dropna(subset=[x_col, y_col])
    diff_df = df[y_col] - df[x_col]
    me = diff_df.mean()
    mae = diff_df.abs().mean()
    # cmap = sns.cubehelix_palette(as_cmap=True, dark=0, light=1, reverse=False)
    cmap = 'Blues'
    fig = plt.figure()
    fig.set_size_inches((9, 9))
    g = sns.JointGrid(x_col, y_col, df,
                xlim=xlim, ylim=ylim)
    hue_vals = []
    for hue_val, hue_df in df.groupby(hue):
        hue_vals.append(hue_val)
        sns.kdeplot(hue_df[x_col], ax=g.ax_marg_x, legend=False, bw=bw)
        sns.kdeplot(hue_df[y_col], ax=g.ax_marg_y, vertical=True, legend=False, bw=bw)
        sns.kdeplot(hue_df[x_col], hue_df[y_col], ax=g.ax_joint,
                     legend=True, gridsize=400, bw=bw, n_levels=n_levels, shade=True, cmap=cmap)
    xfit = np.linspace(xlim[0], xlim[1], 2)
    g.ax_joint.plot(xfit, xfit, linewidth=5, color='k', linestyle='--', alpha=0.5)
    g.ax_joint.text(0.7 * xlim[1], 0.15 * ylim[1], 'Bias={:.3f}'.format(me), fontsize=16)  # add text
    g.ax_joint.text(0.7 * xlim[1], 0.1 * ylim[1], 'Noise={:.3f}'.format(mae), fontsize=16)  # add text
    plt.title(title)
    plt.legend(hue_vals, loc='upper left')
    plt.savefig(os.path.join(output_path, ''.join([output_fname, '.jpg'])),
                dpi=300, bbox_inches='tight')


def standard_curve_plot(dilution_df, fig_path, fig_name, ext, hue=None,
                        zoom=False, col_wrap=3):
    """
    Plot standard curves for ELISA
    :param dataframe dilution_df: dataframe containing serum OD with serial diluition
    :param str fig_path: dir to save the plots
    :param str fig_name: name of the figure file
    :param str ext: figure file extension
    :param str hue: attribute to be plotted with different colors
    :param int col_wrap: number of columns in the facetgrid
    :param bool zoom: If true, output zoom-in of the low OD region
    """
    dilution_df_fit = dilution_df.copy()
    dilution_df_fit = fit2df(dilution_df_fit, fourPL)
    sera_fit_list = dilution_df['serum ID'].unique()
    #%% plot standard curves
    sera_4pl_list = [' '.join([x, 'fit']) for x in sera_fit_list]
    antigens = dilution_df['antigen'].unique()
    markers = 'o'
    style = 'serum type'
    assert not dilution_df.empty, 'Plotting dataframe is empty. Please check the plotting keys'
    palette = sns.color_palette(n_colors=len(dilution_df[hue].unique()))
    print('plotting standard curves...')
    g = sns.lmplot(x="serum dilution", y="OD",
                    hue=hue, hue_order=sera_fit_list, col="antigen", ci='sd', palette=palette, markers=markers,
                     data=dilution_df, col_wrap=col_wrap, fit_reg=False, x_estimator=np.mean)
    palette = sns.color_palette(n_colors=len(dilution_df_fit[hue].unique()))
    for antigen, ax in zip(antigens, g.axes.flat):
        df_fit = dilution_df_fit[(dilution_df_fit['antigen'] == antigen)]
        sns.lineplot(x="serum dilution", y="OD", hue=hue, hue_order=sera_4pl_list, data=df_fit,
                     style=style, palette=palette,
                     ax=ax, legend=False)
        ax.set(xscale="log")
    plt.savefig(os.path.join(fig_path, '.'.join([fig_name, ext])), dpi=300, bbox_inches='tight')

    if zoom:
        for antigen, ax in zip(antigens, g.axes.flat):
            ax.set(ylim=[-0.05, 1.5])
        fig_name += '_zoom'
        plt.savefig(os.path.join(fig_path, '.'.join([fig_name, ext])), dpi=300, bbox_inches='tight')