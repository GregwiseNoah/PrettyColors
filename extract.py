from PIL import Image
import numpy as np
import os
import matplotlib.pyplot as plt

def f(t):
    """Helper-XYZ to LAB."""
    Del  = 6/29

    mask = t > Del**3
    t[mask] = t[mask]**(1/3)
    t[~mask] = 1/3 * t[~mask] * Del**(-2) + 4/29

    return t

def f_inv(t):
    """Inverse Function for LAB to XYZ."""
    Del  = 6/29

    mask = t > Del
    t[mask] = t[mask]**3
    t[~mask] = 3* Del**2 * (t[~mask] - 4/29)

    return t


def rgb_to_lab(pixels_rgb):
    """Takes in (N,3) RGB pixel array and outputs LAB array."""

    # Normalise to [0,1]
    rgb = pixels_rgb.astype(np.float32) / 255.0

    # srgb is non-linear
    mask = rgb > 0.04045 # below this value it needs to be a scaled by 12.92
    rgb[mask]  = ((rgb[mask]  + 0.055) / 1.055) ** 2.4
    rgb[~mask] /= 12.92

    # rgb to cie xyz
    M = np.array([
    [0.4124564, 0.3575761, 0.1804375],
    [0.2126729, 0.7151522, 0.0721750],
    [0.0193339, 0.1191920, 0.9503041],
    ], dtype=np.float32)
    xyzT = np.dot(M, rgb.T)
    xyz = xyzT.T

    # Using D65 whitepoint
    Xn = 0.950489
    Yn = 1
    Zn = 1.088840

    L = 116 * f(xyz[:, 1]/Yn) - 16
    a = 500 * (f(xyz[:, 0]/Xn) - f(xyz[:, 1]/Yn))
    b = 200 * (f(xyz[:, 1]/Yn) - f(xyz[:, 2]/Zn))

    return np.stack([L, a, b], axis=1)


def lab_to_rgb(lab):
    """Takes in LAB pixels and outputs in RGB.   """
    L, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    Xn = 0.950489
    Yn = 1
    Zn = 1.088840

    y = Yn * f_inv((L+16)/116)
    x = Xn * f_inv((L+16)/116 + a / 500)
    z = Zn * f_inv((L+16)/116 - b / 200 )

    xyz = np.stack([x,y,z], axis=1)

    # XYZ to linear RGB
    M_inv = np.array([
        [ 3.2404542, -1.5371385, -0.4985314],
        [-0.9692660,  1.8760108,  0.0415560],
        [ 0.0556434, -0.2040259,  1.0572252],
    ], dtype=np.float32)
    linear = np.dot( M_inv, xyz.T)

    # Gamma encode (linear to sRGB)
    linear = np.clip(linear.T, 0, None)
    srgb = np.where(
        linear > 0.0031308,
        1.055 * linear ** (1 / 2.4) - 0.055,
        12.92 * linear,
    )
    return np.clip(srgb * 255, 0, 255).round().astype(np.uint8)


def extract_pixels(im, max_pixels= 50_000):
    """Return a flat (N,3) uint8 RGB array, downsampled if necessary."""
    rgb = np.array(im.convert("RGB")).reshape(-1, 3)
    if len(rgb) > max_pixels:
        idx = np.random.default_rng(42).choice(len(rgb), size=max_pixels, replace=False)
        rgb = rgb[idx]
    return rgb


def kmeans(pixels_lab, k, max_iter = 20):
    """Run k-means on (N,3) LAB pixels and return (k,3) LAB centres."""
    rng = np.random.default_rng(42) # The answer to the Ultimate Question of Life, the Universe, and Everything
    centers = pixels_lab[rng.choice(len(pixels_lab), k, replace=False)].copy()

    for _ in range(max_iter):
        dists  = np.linalg.norm(pixels_lab[:, None] - centers[None], axis=2)
        labels = np.argmin(dists, axis=1)
        new_centers = np.array([
            pixels_lab[labels == i].mean(axis=0) if (labels == i).any() else centers[i]
            for i in range(k)
        ])
        if np.allclose(centers, new_centers, atol=0.3):
            break
        centers = new_centers

    return centers


def enforce_min_delta_e( centers_lab, pixels_lab, min_de= 25.0,):
    """Only keep colors that are 25 apart in LAB, choose the next unused cluster center    """
    kept = [centers_lab[0]]
    for c in centers_lab[1:]:
        dists_to_kept = [np.linalg.norm(c - k) for k in kept]
        if min(dists_to_kept) >= min_de:
            kept.append(c)
    return np.array(kept)


def luminance(rgb_uint8):
    # returns luminance value for sorting
    r, g, b = rgb_uint8 / 255.0
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def sort_by_luminance(rgb_colors):
    order = sorted(range(len(rgb_colors)), key=lambda i: luminance(rgb_colors[i]))
    return rgb_colors[order]

def extract_colors(img, k = 7, min_de = 25.0):
    """
    Extract `k` perceptually distinct colours from `img`.

    Parameters
    ----------
    k       : number of colours requested
    min_de  : minimum distance between any two colours)
    """
    pixels_rgb = extract_pixels(img)
    pixels_lab = rgb_to_lab(pixels_rgb)

    # Ask for more clusters than needed so the filter has candidates to work with
    centers_lab = kmeans(pixels_lab, k=min(k * 2, len(pixels_rgb)), max_iter=40)

    # Sort by LAB lightness before filtering so we keep the full tonal range
    centers_lab = centers_lab[np.argsort(centers_lab[:, 0])]

    distinct_lab = enforce_min_delta_e(centers_lab, pixels_lab, min_de=min_de)

    if len(distinct_lab) < k:
            distinct_lab = run_check(centers_lab, distinct_lab, k, pixels_lab, min_de) # fix for not enough colors produced

    # Convert back to RGB and sort by perceived luminance
    distinct_rgb = lab_to_rgb(distinct_lab)
    sorted_rgb   = sort_by_luminance(distinct_rgb)
    
    idx = np.linspace(0, len(sorted_rgb)-1, k, dtype=np.int8) # clipping needed to requested value only
    colors = sorted_rgb[idx]
    return [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in colors]


def run_check(centers_lab, distinct_lab, number_of_colors, pixels_lab, min_de_value):
    """If min_de is too high and an image is too uniform, num of colors maybe too low.
        This function reduces min_de until requested number of colors are found.
    """
    while len(distinct_lab) < number_of_colors:
        min_de_value-=3
        distinct_lab = enforce_min_delta_e(centers_lab, pixels_lab=pixels_lab, min_de=min_de_value)
        if min_de_value <=8:
            raise ValueError("Not enough distinct colors in image, try with fewer colors")
    print("Not enough colors found, colors may not be as distinct")
    return distinct_lab

########################## PUBLIC FUNCTIONS ##########################

def line_colors( image_path, number_of_colors= 7, min_de= 25.0, save= True, preview = False):
    """Extract a categorical palette and optionally preview it."""
    with Image.open(image_path) as im:
        colors = extract_colors(im, k=number_of_colors, min_de=min_de)


    if preview:
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10, 1))
        for i, color in enumerate(colors):
            ax.fill_between([i, i + 1], 0, 1, color=color)
        ax.set_xlim(0, len(colors))
        ax.set_axis_off()
        plt.tight_layout()
        plt.show()

    return colors


def sequential_colors( image_path, save = True, preview= False ):
    """Build a smooth LinearSegmentedColormap from the image's tonal range."""
    from matplotlib.colors import LinearSegmentedColormap

    with Image.open(image_path) as im:
        colors = extract_colors(im, k=4, min_de=15)   # smaller step size for a smoother gradient

    name = os.path.splitext(os.path.basename(image_path))[0]
    CMAP =  LinearSegmentedColormap.from_list(name, colors, N=256)

    if preview:
        fig, ax = plt.subplots(figsize=(10, 1))
        number_of_colors = 256  # or any larger value to show the gradient
        for i in range(number_of_colors):
            ax.fill_between([i, i + 1], 0, 1, color=CMAP(i / (number_of_colors - 1)))  # Sampling evenly across the colormap
        ax.set_xlim(0, number_of_colors)
        ax.set_axis_off()
        plt.tight_layout()
        plt.show()

    return CMAP

