import copy
from pathlib import Path
from typing import Any, Dict, Union
from PIL import Image
import logging

import numpy as np
from ..core import AbstractVersionedDataSet, DataSetError, Version

from ...ops.numpy_ops import to_channel_first_arr, to_channel_last_arr

log = logging.getLogger(__name__)


class ImagesLocalDataSet(AbstractVersionedDataSet):
    def __init__(
        self,
        path: str,
        load_args: Dict[str, Any] = None,
        save_args: Dict[str, Any] = {"suffix": ".jpg"},
        channel_first=False,
        reverse_color=False,
        version: Version = None,
    ) -> None:

        super().__init__(
            filepath=Path(path), version=version, exists_function=self._exists,
        )
        self._load_args = load_args
        self._save_args = save_args
        self._channel_first = channel_first
        self._reverse_color = reverse_color

    def _load(self) -> Any:

        load_path = Path(self._get_load_path())

        load_args = copy.deepcopy(self._load_args)
        load_args = load_args or dict()

        dict_structure = load_args.pop("dict_structure", True)
        as_numpy = load_args.pop("as_numpy", True)

        channel_first = self._channel_first
        reverse_color = self._reverse_color

        if load_path.is_dir():
            images_dict = {}
            for p in load_path.glob("*"):
                img = load_image(
                    p,
                    load_args,
                    as_numpy=as_numpy,
                    channel_first=channel_first,
                    reverse_color=reverse_color,
                )
                images_dict[p.stem] = img

            if dict_structure is None:
                return list(images_dict.values())
            if dict_structure == "sep_names":
                return dict(
                    images=list(images_dict.values()), names=list(images_dict.keys())
                )

            return images_dict

        else:
            return load_image(
                load_path,
                load_args,
                as_numpy=self.as_numpy,
                channel_first=channel_first,
                reverse_color=reverse_color,
            )

    def _save(self, data: Union[dict, list, np.ndarray, type(Image.Image)]) -> None:
        save_path = Path(self._get_save_path())
        save_path.parent.mkdir(parents=True, exist_ok=True)
        p = save_path

        save_args = copy.deepcopy(self._save_args)
        save_args = save_args or dict()
        suffix = save_args.pop("suffix", ".jpg")
        mode = save_args.pop("mode", None)
        upper = save_args.pop("upper", None)
        lower = save_args.pop("lower", None)
        to_scale = (upper is not None) or (lower is not None)

        if isinstance(data, dict):
            images = list(data.values())
            names = list(data.keys())
            if "names" in names and "images" in names:
                images = data.get("images")
                names = data.get("names")

        else:
            images = data
            names = None

        if hasattr(images, "save"):
            if not to_scale:
                img = images
                img.save(p, **save_args)
                return None
            else:
                images = np.asarray(images)

        if isinstance(images, np.ndarray):
            if self._channel_first:
                images = to_channel_last_arr(images)
            if self._reverse_color:
                images = ReverseChannel(channel_first=self._channel_first)(images)
            if images.ndim in {2, 3}:
                img = images
                img = scale(lower=lower, upper=upper)(img)
                img = np.squeeze(img)
                img = Image.fromarray(img, mode=mode)
                img.save(p, **save_args)
                return None
            elif images.ndim in {4}:
                images = scale(lower=lower, upper=upper)(images)
                dataset = Np3DArrDataset(images)
            else:
                raise ValueError(
                    "Unsupported number of dimensions: {}".format(images.ndim)
                )
        elif hasattr(images, "__getitem__") and hasattr(images, "__len__"):
            if not to_scale:
                p.mkdir(parents=True, exist_ok=True)
                for i, img in enumerate(images):
                    if isinstance(img, np.ndarray):
                        if self._channel_first:
                            img = to_channel_last_arr(img)
                        if self._reverse_color:
                            img = ReverseChannel(channel_first=self._channel_first)(img)
                        img = np.squeeze(img)
                        img = Image.fromarray(img)
                    name = names[i] if names else "{:05d}".format(i)
                    s = p / "{}{}".format(name, suffix)
                    img.save(s, **save_args)
                return None
            else:
                dataset = Np3DArrDatasetFromList(
                    images, transform=scale(lower=lower, upper=upper)
                )
        else:
            raise ValueError("Unsupported data type: {}".format(type(images)))

        p.mkdir(parents=True, exist_ok=True)
        for i in range(len(dataset)):
            img = dataset[i]
            if isinstance(img, (tuple, list)):
                img = img[0]
            if self._channel_first:
                img = to_channel_last_arr(img)
            if self._reverse_color:
                img = ReverseChannel(channel_first=self._channel_first)(img)
            img = np.squeeze(img)
            img = Image.fromarray(img, mode=mode)
            name = names[i] if names else "{:05d}".format(i)
            s = p / "{}{}".format(name, suffix)
            img.save(s, **save_args)
        return None

    def _describe(self) -> Dict[str, Any]:
        return dict(
            filepath=self._filepath,
            load_args=self._save_args,
            save_args=self._save_args,
            channel_first=self._channel_first,
            reverse_color=self._reverse_color,
            version=self._version,
        )

    def _exists(self) -> bool:
        try:
            path = self._get_load_path()
        except DataSetError:
            return False
        return Path(path).exists()


def load_image(
    load_path, load_args, as_numpy=False, channel_first=False, reverse_color=False
):
    with load_path.open("rb") as local_file:
        img = Image.open(local_file, **load_args)
        if as_numpy:
            img = np.asarray(img)
            if channel_first:
                img = to_channel_first_arr(img)
            if reverse_color:
                img = ReverseChannel(channel_first=channel_first)(img)
        return img


def scale(**kwargs):
    def _scale(a):
        lower = kwargs.get("lower")
        upper = kwargs.get("upper")
        if (lower is not None) or (upper is not None):
            max_val = a.max()
            min_val = a.min()
            stat_dict = dict(max_val=max_val, min_val=min_val)
            log.info(stat_dict)
            upper = upper or max_val
            lower = lower or min_val
            a = (
                ((a - min_val) / (max_val - min_val)) * (upper - lower) + lower
            ).astype(np.uint8)
        return a

    return _scale


class Np3DArrDataset:
    def __init__(self, a):
        self.a = a

    def __getitem__(self, index):
        return self.a[index, ...]

    def __len__(self):
        return len(self.a)


class Np3DArrDatasetFromList:
    def __init__(self, a, transform=None):
        self.a = a
        self.transform = transform

    def __getitem__(self, index):
        item = np.asarray(self.a[index])
        if self.transform:
            item = self.transform(item)
        return item

    def __len__(self):
        return len(self.a)


def reverse_channel(a, channel_first=False):
    if a.ndim == 3:
        if channel_first:
            return a[::-1, :, :]
        else:
            return a[:, :, ::-1]
    if a.ndim == 4:
        if channel_first:
            return a[:, ::-1, :, :]
        else:
            return a[:, :, :, ::-1]
    return a


class ReverseChannel:
    def __init__(self, channel_first=False):
        self.channel_first = channel_first

    def __call__(self, a):
        return reverse_channel(a, channel_first=self.channel_first)
