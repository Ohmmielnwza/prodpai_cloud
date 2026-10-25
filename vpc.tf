# -----
# VPC with 2 AZ 
# one public and private each az
# has internal-gw for public access and has nat-gw for direch ssh
# -----
resource "aws_vpc" "main" {
  cidr_block = local.vpc_cdir

  tags = merge(
    local.common_tag,
    {
      Name = "${var.environment}-vpc"
    }
  )

}

# Get are avlilable az form AWS
data "aws_availability_zones" "available" {
  state = "available"
}

# Create IGW
resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id

  tags = merge(
    local.common_tag,
    {
      Name = "${var.environment}-igw"
    }
  )
}

# Create public-zone on each AZ
resource "aws_subnet" "public_zone" {
  count = length(local.public_subnets)

  vpc_id            = aws_vpc.main.id
  cidr_block        = local.public_subnets[count.index]
  availability_zone = element(local.azs, count.index)

  tags = merge(
    local.common_tag,
    {
      Name = "${var.environment}-public-${local.azs[count.index]}"
    }
  )
}

# Create routing table for public to IGW
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }

  tags = merge(
    local.common_tag,
    {
      Name = "${var.environment}-public"
    }
  )
}

# Create routing for public-zone to IGW
resource "aws_route_table_association" "public" {
  count = length(local.public_subnets)

  subnet_id      = aws_subnet.public_zone[count.index].id
  route_table_id = aws_route_table.public.id
}

# Reserve Elastic IP
resource "aws_eip" "nat" {
  domain = "vpc"

  tags = merge(
    local.common_tag,
    {
      Name = "${var.environment}-nat"
    }
  )

  depends_on = [aws_internet_gateway.igw]

}

# Associate an EIP with NAT-gw
resource "aws_nat_gateway" "nat" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public_zone[0].id

  tags = merge(
    local.common_tag,
    {
      Name = "${var.environment}-gw"
    }
  )
}

# Create Private-zone each AZ
resource "aws_subnet" "private_zone" {
  for_each = local.private_subnets

  vpc_id            = aws_vpc.main.id
  cidr_block        = each.value.cidr
  availability_zone = each.value.az

  tags = merge(
    local.common_tag,
    {
      Name = "${var.environment}-private-${each.value.az}"
    }
  )
}

# Create Routing table for NAT-gw
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.nat.id
  }

  tags = merge(
    local.common_tag,
    {
      Name = "${var.environment}-private"
    }
  )
}

# Create routing for Private-zone to NAT-gw
resource "aws_route_table_association" "private" {
  for_each = aws_subnet.private_zone

  subnet_id      = each.value.id
  route_table_id = aws_route_table.private.id
}
