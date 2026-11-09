document.addEventListener("change", (event) => {
    if (event.target.name !== "demo_profile") {
        return;
    }
    const option = event.target.selectedOptions[0];
    const form = event.target.closest("form");
    for (const [field, value] of [
        ["lang", option.dataset.language],
        ["country_code", option.dataset.country],
    ]) {
        if (value && form.elements[field]) {
            const select = form.elements[field];
            const matchingOption = Array.from(select.options).find(
                (item) => item.value.toLowerCase() === value.toLowerCase()
            );
            if (matchingOption) {
                select.value = matchingOption.value;
            }
        }
    }
});
