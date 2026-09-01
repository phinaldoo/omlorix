(function () {
    const matchesDependencyValue = (currentValue, requiredValue) => {
        if (Array.isArray(requiredValue)) {
            const normalizedRequiredValues = requiredValue.map((value) => String(value));
            if (Array.isArray(currentValue)) {
                return normalizedRequiredValues.some((value) => currentValue.includes(value));
            }
            return normalizedRequiredValues.includes(String(currentValue));
        }

        if (Array.isArray(currentValue)) {
            return currentValue.includes(String(requiredValue));
        }

        if (typeof requiredValue === 'boolean') {
            return currentValue === requiredValue;
        }

        return String(currentValue) === String(requiredValue);
    };

    if (typeof window !== 'undefined') {
        window.SchemaDependencyUtils = {
            ...(window.SchemaDependencyUtils || {}),
            matchesDependencyValue,
        };
    }
})();
