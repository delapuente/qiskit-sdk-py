# -*- coding: utf-8 -*-

# Copyright 2018, IBM.
#
# This source code is licensed under the Apache License, Version 2.0 found in
# the LICENSE.txt file in the root directory of this source tree.

"""Building blocks for Qiskit validated classes.

This module provides the ``BaseSchema`` and ``BaseModel`` classes as the main
building blocks for defining objects (Models) that conform to a specification
(Schema) and are validated at instantiation, along with providing facilities
for being serialized and deserialized.

Implementors are recommended to subclass the two classes, and "binding" them
together by using ``bind_schema``::

    class PersonSchema(BaseSchema):
        name = String(required=True)

    @bind_schema(PersonSchema)
    class Person(BaseModel):
        pass
"""
from functools import wraps
from types import SimpleNamespace

from marshmallow import ValidationError, MarshalResult
from marshmallow import Schema, post_dump, post_load

from qiskit.validation.utils import is_validating, VALID_MODEL


class BaseSchema(Schema):
    """Base class for Schemas for validated Qiskit classes.

    Provides convenience functionality for the Qiskit common use case:

    * deserialization into class instances instead of dicts.
    * handling of unknown attributes not defined in the schema.

    Attributes:
         model_cls (type): class used to instantiate the instance. The
         constructor is passed all named parameters from deserialization.
    """

    model_cls = SimpleNamespace

    black_list = ['__is_valid__']

    def dump(self, obj, many=None, update_fields=True, **kwargs):
        if is_validating():
            if isinstance(obj, self.model_cls):
                if self.is_valid_instance(obj):
                    return MarshalResult(VALID_MODEL, [])

            else:
                error = ValidationError(
                    'Not an instance of {}'.format(self.model_cls), data=obj)
                return MarshalResult(obj, [error])

        return super().dump(obj, many=many, update_fields=update_fields, **kwargs)

    def load(self, data, many=None, partial=None):
        if data is VALID_MODEL:
            return MarshalResult(data, [])

        return super().load(data, many=many, partial=partial)

    @staticmethod
    def validate_model(instance):
        """Marks the model as valid.

        This method is used by the model's validation machinery to prevent
        validating the same model twice.
        """
        instance.__is_valid__ = True

    @staticmethod
    def invalidate_instance(instance):
        """Marks the model as potentially invalid.

        This method is used by the model's validation machinery to allow
        mutation and re-validation although the developer is responsible of
        manually calling model's methods ``_invalidate()`` and ``_validate()``
        to ensure the integrity of the model.
        """
        instance.__is_valid__ = False

    @staticmethod
    def is_valid_instance(instance):
        """Checks validity of a model.

        Return True is the model was marked as valid or False otherwise.
        """
        return getattr(instance, '__is_valid__', False)

    @post_dump(pass_original=True, pass_many=True)
    def dump_additional_data(self, valid_data, many, original_data):
        """Include unknown fields after dumping.

        Unknown fields are added with no processing at all.

        Args:
            valid_data (dict or list): data collected and returned by ``dump()``.
            many (bool): if True, data and original_data are a list.
            original_data (object or list): object passed to ``dump()`` in the
                first place.

        Returns:
            dict: the same ``valid_data`` extended with the unknown attributes.

        Inspired by https://github.com/marshmallow-code/marshmallow/pull/595.
        """
        if many:
            for i, _ in enumerate(valid_data):
                additional_keys = set(original_data[i].__dict__) -\
                                  set(valid_data[i]) - set(self.black_list)
                for key in additional_keys:
                    valid_data[i][key] = getattr(original_data[i], key)
        else:
            additional_keys = set(original_data.__dict__) - set(valid_data) - set(self.black_list)
            for key in additional_keys:
                valid_data[key] = getattr(original_data, key)

        return valid_data

    @post_load(pass_original=True, pass_many=True)
    def load_additional_data(self, valid_data, many, original_data):
        """Include unknown fields after load.

        Unknown fields are added with no processing at all.

        Args:
            valid_data (dict or list): validated data returned by ``load()``.
            many (bool): if True, data and original_data are a list.
            original_data (dict or list): data passed to ``load()`` in the
                first place.

        Returns:
            dict: the same ``valid_data`` extended with the unknown attributes.

        Inspired by https://github.com/marshmallow-code/marshmallow/pull/595.
        """
        if many:
            for i, _ in enumerate(valid_data):
                additional_keys = set(original_data[i]) - set(valid_data[i])
                for key in additional_keys:
                    valid_data[i][key] = original_data[i][key]
        else:
            additional_keys = set(original_data) - set(valid_data)
            for key in additional_keys:
                valid_data[key] = original_data[key]

        return valid_data

    @post_load
    def _make_model(self, data):
        return self.model_cls(**data, __validity_warranty__=True)


class _SchemaBinder:
    """Helper class for the parametrized decorator ``bind_schema``."""

    def __init__(self, schema_cls):
        """Get the schema for the decorated model."""
        self._schema_cls = schema_cls

    def __call__(self, model_cls):
        """Augment the model class with the validation API.

        See the docs for ``bind_schema`` for further information.
        """
        # Check for double binding of schemas.
        if self._schema_cls.__dict__.get('model_cls', None) is not None:
            raise ValueError(
                'The schema {} can not be bound twice. It is already bound to '
                '{}. If you want to reuse the schema, use '
                'subclassing'.format(self._schema_cls, self._schema_cls.model_cls))

        # Set a reference to the Model in the Schema, and viceversa.
        self._schema_cls.model_cls = model_cls
        model_cls.schema = self._schema_cls()

        # Append the methods to the Model class.
        model_cls.to_dict = self._to_dict
        model_cls.from_dict = classmethod(self._from_dict)
        model_cls._validate = self._validate
        model_cls._invalidate = self._invalidate
        model_cls.__init__ = self._validate_after_init(model_cls.__init__)

        return model_cls

    @staticmethod
    def _to_dict(instance):
        """Serialize the model into a Python dict of simple types."""
        data, errors = instance.schema.dump(instance)
        if errors:
            raise ValidationError(errors)
        return data

    @staticmethod
    def _validate(instance):
        """Validate the internal representation of the instance."""
        schema = instance.schema
        if not schema.is_valid_instance(instance):
            # pylint: disable=unused-variable
            __is_validating__ = True
            errors = instance.schema.validate(instance.to_dict())
            __is_validating__ = False
            if errors:
                raise ValidationError(errors)

            instance.schema.validate_model(instance)

    @staticmethod
    def _invalidate(instance):
        """Invalidates the instance making ``_validate`` to rerun all checks."""
        instance.schema.invalidate_instance(instance)

    @staticmethod
    def _from_dict(decorated_cls, dict_):
        """Deserialize a dict of simple types into an instance of this class."""
        data, errors = decorated_cls.schema.load(dict_)
        if errors:
            raise ValidationError(errors)
        return data

    @staticmethod
    def _validate_after_init(init_method):
        """Add validation after instantiation."""

        @wraps(init_method)
        def _decorated(self, **kwargs):
            has_validity_warranty = kwargs.pop('__validity_warranty__', False)
            init_method(self, **kwargs)

            if has_validity_warranty:
                self.schema.validate_model(self)
            else:
                self._validate()

        return _decorated


def bind_schema(schema):
    """Class decorator for adding schema validation to its instances.

    Instances of the decorated class are automatically validated after
    instantiation and they are augmented to allow further validations with the
    private method ``_validate()``.

    Since validation can be an expensive operation, once validated, an instance
    is considered valid forever or until calling ``_invalidate()``.

    If an instance is modified in such a way it can be invalidated, the
    instance can call ``_invalidate()`` to force the following call to
    ``_validate()`` to rerun all the checks.

    The decorator also adds the class attribute ``schema`` with the schema used
    for validation.

    To ease serialization/deserialization to/from simple Python objects,
    classes are provided with ``to_dict`` and ``from_dict`` instance and class
    methods respectively.

    The same schema cannot be bound more than once. If you need to reuse a
    schema for a different class, create a new schema subclassing the one you
    want to reuse and leave the new empty::

        class MySchema(BaseSchema):
            title = String()

        class AnotherSchema(MySchema):
            pass

        @bind_schema(MySchema):
        class MyModel(BaseModel):
            pass

        @bind_schema(AnotherSchema):
        class AnotherModel(BaseModel):
            pass

    Raises:
        ValueError: when trying to bind the same schema more than once.

    Return:
        type: the same class with validation capabilities.
    """
    return _SchemaBinder(schema)


def _base_model_from_kwargs(cls, kwargs):
    """Helper for BaseModel.__reduce__, expanding kwargs."""
    return cls(**kwargs)


class BaseModel(SimpleNamespace):
    """Base class for Models for validated Qiskit classes."""
    def __reduce__(self):
        """Custom __reduce__ for allowing pickling and unpickling.

        Customize the reduction in order to allow serialization, as the
        BaseModels need to be pickled during the use of futures by the backends.
        Instead of returning the class, a helper is used in order to pass the
        arguments as **kwargs, as it is needed by SimpleNamespace and the
        standard __reduce__ only allows passing args as a tuple.
        """
        return _base_model_from_kwargs, (self.__class__, self.__dict__)


class ObjSchema(BaseSchema):
    """Generic object schema."""
    pass


@bind_schema(ObjSchema)
class Obj(BaseModel):
    """Generic object in a Model."""
    pass
