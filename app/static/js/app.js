document.addEventListener('DOMContentLoaded', () => {
	const items = document.querySelectorAll('.card');
	items.forEach((item, index) => {
		item.animate(
			[
				{ transform: 'translateY(12px)', opacity: 0 },
				{ transform: 'translateY(0)', opacity: 1 },
			],
			{
				duration: 350,
				delay: index * 50,
				easing: 'ease-out',
				fill: 'both',
			}
		);
	});
});
