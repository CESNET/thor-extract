"""A demonstration plugin: extra features registered from outside the vendored code."""

from extractor.transformations.base_transformation import Transformation

from thor_extract import extensions


class NameLengthSquared(Transformation):
    def transform(self, df):
        df["probe_name_len_squared"] = df["domain_name"].apply(len) ** 2
        return df

    @property
    def features(self):
        return {"probe_name_len_squared": "Int64"}


def register():
    extensions.register("probe_name_len_squared", NameLengthSquared, before="drop")
